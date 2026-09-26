/*
 * Copyright 2026 Multikernel Technologies, Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * One kerf exec session: runs a command for the host and relays its stdio.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <poll.h>
#include <pty.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/signalfd.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#include "proto.h"
#include "session.h"

/*
 * After the command exits, output still in flight is drained for at most
 * this long, so a background process holding the pty or pipes cannot keep
 * the session open.
 */
#define DRAIN_TIMEOUT_MS 100

struct open_req {
    uint16_t flags;
    uint32_t uid;
    uint32_t gid;
    uint16_t ngroups;
    gid_t *groups;
    uint16_t rows;
    uint16_t cols;
    char *cwd;
    char **argv;
    char **envp;
};

enum child_stage {
    STAGE_TTY,
    STAGE_SETGROUPS,
    STAGE_SETGID,
    STAGE_SETUID,
    STAGE_CHDIR,
    STAGE_EXEC,
};

struct child_err {
    int stage;
    int err;
};

static struct {
    int sock;
    int sig_fd;
    int master;
    int in_fd;
    int out_fd;
    int err_fd;
    pid_t pid;
    int stdin_eof;
    size_t in_len;
    size_t rx_len;
    unsigned char in_buf[KERF_STDIN_WINDOW];
    unsigned char rx[KERF_HDR_LEN + KERF_MAX_PAYLOAD];
    unsigned char tx[KERF_HDR_LEN + KERF_MAX_PAYLOAD];
} s;

static void hangup(void) __attribute__((noreturn));

static void hangup(void)
{
    if (s.pid > 0)
        kill(-s.pid, SIGHUP);
    _exit(0);
}

static int send_all(const unsigned char *buf, size_t len)
{
    while (len > 0) {
        ssize_t n = send(s.sock, buf, len, MSG_NOSIGNAL);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        buf += n;
        len -= n;
    }
    return 0;
}

static void send_frame(uint16_t type, const void *payload, size_t len)
{
    put_le32(s.tx, len);
    put_le16(s.tx + 4, type);
    put_le16(s.tx + 6, 0);
    if (len)
        memcpy(s.tx + KERF_HDR_LEN, payload, len);
    if (send_all(s.tx, KERF_HDR_LEN + len) < 0)
        hangup();
}

static void send_error(int err, const char *msg)
{
    unsigned char buf[512];
    size_t len = strnlen(msg, sizeof(buf) - 5);

    put_le32(buf, err);
    memcpy(buf + 4, msg, len);
    buf[4 + len] = '\0';
    send_frame(KERF_ERROR, buf, 5 + len);
}

static int recv_exact(unsigned char *buf, size_t len)
{
    while (len > 0) {
        ssize_t n = recv(s.sock, buf, len, 0);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0)
            return -1;
        buf += n;
        len -= n;
    }
    return 0;
}

static int parse_open(unsigned char *p, size_t len, struct open_req *o)
{
    uint16_t argc, envc;
    size_t off = KERF_OPEN_FIXED_LEN;
    char *str, *end;

    if (len < KERF_OPEN_FIXED_LEN)
        return -EINVAL;
    if (get_le16(p) != KERF_PROTO_VERSION)
        return -EPROTO;

    o->flags = get_le16(p + 2);
    o->uid = get_le32(p + 4);
    o->gid = get_le32(p + 8);
    o->ngroups = get_le16(p + 12);
    o->rows = get_le16(p + 14);
    o->cols = get_le16(p + 16);
    argc = get_le16(p + 18);
    envc = get_le16(p + 20);

    if (argc == 0 || len - off < (size_t)o->ngroups * 4)
        return -EINVAL;

    o->groups = calloc(o->ngroups + 1, sizeof(gid_t));
    o->argv = calloc(argc + 1, sizeof(char *));
    o->envp = calloc(envc + 1, sizeof(char *));
    if (!o->groups || !o->argv || !o->envp)
        return -ENOMEM;

    for (int i = 0; i < o->ngroups; i++, off += 4)
        o->groups[i] = get_le32(p + off);

    str = (char *)p + off;
    end = (char *)p + len;
    for (int i = 0; i < 1 + argc + envc; i++) {
        char *nul = memchr(str, '\0', end - str);
        if (!nul)
            return -EINVAL;
        if (i == 0)
            o->cwd = str;
        else if (i <= argc)
            o->argv[i - 1] = str;
        else
            o->envp[i - 1 - argc] = str;
        str = nul + 1;
    }
    return str == end ? 0 : -EINVAL;
}

static void read_open(struct open_req *o)
{
    static unsigned char payload[KERF_MAX_PAYLOAD];
    unsigned char hdr[KERF_HDR_LEN];
    uint32_t len;
    int err;

    if (recv_exact(hdr, sizeof(hdr)) < 0)
        _exit(0);
    len = get_le32(hdr);
    if (get_le16(hdr + 4) != KERF_OPEN || len > KERF_MAX_PAYLOAD)
        _exit(0);
    if (recv_exact(payload, len) < 0)
        _exit(0);

    err = parse_open(payload, len, o);
    if (err == -EPROTO) {
        send_error(EPROTO, "unsupported protocol version");
        _exit(0);
    }
    if (err < 0) {
        send_error(-err, "malformed OPEN request");
        _exit(0);
    }
}

static void child_fail(int fd, int stage) __attribute__((noreturn));

static void child_fail(int fd, int stage)
{
    struct child_err ce = { stage, errno };

    write(fd, &ce, sizeof(ce));
    _exit(127);
}

static void run_child(struct open_req *o, int slave, int in_r, int out_w,
                      int err_w, int err_pipe) __attribute__((noreturn));

static void run_child(struct open_req *o, int slave, int in_r, int out_w,
                      int err_w, int err_pipe)
{
    sigset_t none;

    sigemptyset(&none);
    sigprocmask(SIG_SETMASK, &none, NULL);
    signal(SIGPIPE, SIG_DFL);
    setsid();

    if (o->flags & KERF_OPEN_TTY) {
        if (ioctl(slave, TIOCSCTTY, 0) < 0)
            child_fail(err_pipe, STAGE_TTY);
        in_r = out_w = err_w = slave;
    }
    dup2(in_r, STDIN_FILENO);
    dup2(out_w, STDOUT_FILENO);
    dup2(err_w, STDERR_FILENO);

    if (o->flags & KERF_OPEN_USER) {
        if (setgroups(o->ngroups, o->groups) < 0)
            child_fail(err_pipe, STAGE_SETGROUPS);
        if (setgid(o->gid) < 0)
            child_fail(err_pipe, STAGE_SETGID);
        if (setuid(o->uid) < 0)
            child_fail(err_pipe, STAGE_SETUID);
    }
    if (chdir(o->cwd) < 0)
        child_fail(err_pipe, STAGE_CHDIR);

    /* execvpe() would search the PATH kerf-init has, not the one sent. */
    environ = o->envp;
    execvp(o->argv[0], o->argv);
    child_fail(err_pipe, STAGE_EXEC);
}

static void report_child_err(struct open_req *o, struct child_err *ce)
{
    static const char *const names[] = {
        [STAGE_TTY] = "controlling tty",
        [STAGE_SETGROUPS] = "setgroups",
        [STAGE_SETGID] = "setgid",
        [STAGE_SETUID] = "setuid",
    };
    char msg[400];

    if (ce->stage == STAGE_EXEC)
        snprintf(msg, sizeof(msg), "exec %.200s: %s", o->argv[0], strerror(ce->err));
    else if (ce->stage == STAGE_CHDIR)
        snprintf(msg, sizeof(msg), "chdir %.200s: %s", o->cwd, strerror(ce->err));
    else
        snprintf(msg, sizeof(msg), "%s: %s", names[ce->stage], strerror(ce->err));
    send_error(ce->err, msg);
}

static void set_nonblock(int fd)
{
    if (fd >= 0)
        fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
}

static void start_command(struct open_req *o)
{
    int slave = -1, in_r = -1, out_w = -1, err_w = -1;
    int p[2], errp[2];
    struct child_err ce;
    ssize_t n;

    s.master = s.in_fd = s.out_fd = s.err_fd = -1;

    if (o->flags & KERF_OPEN_TTY) {
        struct winsize ws = { .ws_row = o->rows, .ws_col = o->cols };

        if (openpty(&s.master, &slave, NULL, NULL,
                    o->rows && o->cols ? &ws : NULL) < 0)
            goto fail;
        fcntl(s.master, F_SETFD, FD_CLOEXEC);
        fcntl(slave, F_SETFD, FD_CLOEXEC);
        s.out_fd = s.master;
        if (o->flags & KERF_OPEN_STDIN)
            s.in_fd = fcntl(s.master, F_DUPFD_CLOEXEC, 3);
    } else {
        if (o->flags & KERF_OPEN_STDIN) {
            if (pipe2(p, O_CLOEXEC) < 0)
                goto fail;
            in_r = p[0];
            s.in_fd = p[1];
        } else {
            in_r = open("/dev/null", O_RDONLY | O_CLOEXEC);
        }
        if (pipe2(p, O_CLOEXEC) < 0)
            goto fail;
        s.out_fd = p[0];
        out_w = p[1];
        if (pipe2(p, O_CLOEXEC) < 0)
            goto fail;
        s.err_fd = p[0];
        err_w = p[1];
    }

    if (pipe2(errp, O_CLOEXEC) < 0)
        goto fail;

    s.pid = fork();
    if (s.pid < 0)
        goto fail;
    if (s.pid == 0) {
        close(errp[0]);
        run_child(o, slave, in_r, out_w, err_w, errp[1]);
    }

    close(errp[1]);
    if (slave >= 0)
        close(slave);
    if (in_r >= 0)
        close(in_r);
    if (out_w >= 0)
        close(out_w);
    if (err_w >= 0)
        close(err_w);

    do {
        n = read(errp[0], &ce, sizeof(ce));
    } while (n < 0 && errno == EINTR);
    close(errp[0]);

    if (n == sizeof(ce)) {
        waitpid(s.pid, NULL, 0);
        s.pid = -1;
        report_child_err(o, &ce);
        _exit(0);
    }

    set_nonblock(s.in_fd);
    set_nonblock(s.out_fd);
    set_nonblock(s.err_fd);
    return;

fail:
    send_error(errno, strerror(errno));
    _exit(0);
}

/* Forward one read from a command stream; closes the fd at EOF or EIO. */
static void pump(int *fd, uint16_t type)
{
    static unsigned char buf[KERF_MAX_PAYLOAD];
    ssize_t n = read(*fd, buf, sizeof(buf));

    if (n > 0) {
        send_frame(type, buf, n);
        return;
    }
    if (n < 0 && (errno == EAGAIN || errno == EINTR))
        return;
    if (*fd != s.master)
        close(*fd);
    *fd = -1;
}

static void send_ack(size_t n)
{
    unsigned char payload[4];

    put_le32(payload, n);
    send_frame(KERF_STDIN_ACK, payload, sizeof(payload));
}

static void close_stdin(void)
{
    if (s.master >= 0) {
        struct termios t;

        if (tcgetattr(s.master, &t) == 0)
            write(s.in_fd, &t.c_cc[VEOF], 1);
    }
    close(s.in_fd);
    s.in_fd = -1;
}

/* The command will never read what is buffered; acknowledge it anyway. */
static void drop_stdin(void)
{
    if (s.in_len)
        send_ack(s.in_len);
    s.in_len = 0;
    close(s.in_fd);
    s.in_fd = -1;
}

static void flush_stdin(void)
{
    ssize_t n = write(s.in_fd, s.in_buf, s.in_len);

    if (n > 0) {
        memmove(s.in_buf, s.in_buf + n, s.in_len - n);
        s.in_len -= n;
        send_ack(n);
    } else if (n < 0 && errno != EAGAIN && errno != EINTR) {
        drop_stdin();
        return;
    }
    if (s.in_len == 0 && s.stdin_eof)
        close_stdin();
}

static void handle_frame(uint16_t type, unsigned char *p, uint32_t len)
{
    switch (type) {
    case KERF_STDIN:
        if (s.in_len + len > KERF_STDIN_WINDOW)
            hangup();
        if (s.in_fd < 0 || s.stdin_eof) {
            send_ack(len);
            break;
        }
        memcpy(s.in_buf + s.in_len, p, len);
        s.in_len += len;
        break;
    case KERF_STDIN_EOF:
        s.stdin_eof = 1;
        if (s.in_fd >= 0 && s.in_len == 0)
            close_stdin();
        break;
    case KERF_RESIZE:
        if (len != 4)
            hangup();
        if (s.master >= 0) {
            struct winsize ws = { .ws_row = get_le16(p), .ws_col = get_le16(p + 2) };
            ioctl(s.master, TIOCSWINSZ, &ws);
        }
        break;
    case KERF_SIGNAL:
        if (len != 4)
            hangup();
        kill(-s.pid, get_le32(p));
        break;
    default:
        hangup();
    }
}

static void process_frames(void)
{
    while (s.rx_len >= KERF_HDR_LEN) {
        uint32_t len = get_le32(s.rx);
        size_t total = KERF_HDR_LEN + len;

        if (len > KERF_MAX_PAYLOAD)
            hangup();
        if (s.rx_len < total)
            break;
        handle_frame(get_le16(s.rx + 4), s.rx + KERF_HDR_LEN, len);
        memmove(s.rx, s.rx + total, s.rx_len - total);
        s.rx_len -= total;
    }
}

static void receive(void)
{
    ssize_t n = recv(s.sock, s.rx + s.rx_len, sizeof(s.rx) - s.rx_len, MSG_DONTWAIT);

    if (n == 0 || (n < 0 && errno != EAGAIN && errno != EINTR))
        hangup();
    if (n > 0)
        s.rx_len += n;
    process_frames();
}

static long now_ms(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void finish(int status) __attribute__((noreturn));

static void finish(int status)
{
    unsigned char exit_payload[2];
    long deadline = now_ms() + DRAIN_TIMEOUT_MS;

    for (;;) {
        struct pollfd pfd[2] = {
            { .fd = s.out_fd, .events = POLLIN },
            { .fd = s.err_fd, .events = POLLIN },
        };
        long left = deadline - now_ms();

        if ((s.out_fd < 0 && s.err_fd < 0) || left <= 0)
            break;
        if (poll(pfd, 2, left) <= 0)
            break;
        if (pfd[0].revents)
            pump(&s.out_fd, KERF_STDOUT);
        if (pfd[1].revents)
            pump(&s.err_fd, KERF_STDERR);
    }

    if (WIFSIGNALED(status)) {
        exit_payload[0] = 1;
        exit_payload[1] = WTERMSIG(status);
    } else {
        exit_payload[0] = 0;
        exit_payload[1] = WEXITSTATUS(status);
    }
    send_frame(KERF_EXIT, exit_payload, sizeof(exit_payload));
    _exit(0);
}

static void relay(void) __attribute__((noreturn));

static void relay(void)
{
    for (;;) {
        struct pollfd pfd[5] = {
            { .fd = s.sig_fd, .events = POLLIN },
            { .fd = s.sock, .events = POLLIN },
            { .fd = s.in_len > 0 ? s.in_fd : -1, .events = POLLOUT },
            { .fd = s.out_fd, .events = POLLIN },
            { .fd = s.err_fd, .events = POLLIN },
        };
        struct signalfd_siginfo si;
        int status;

        if (poll(pfd, 5, -1) < 0) {
            if (errno == EINTR)
                continue;
            hangup();
        }

        if (pfd[3].revents)
            pump(&s.out_fd, KERF_STDOUT);
        if (pfd[4].revents)
            pump(&s.err_fd, KERF_STDERR);
        /* A pty master whose slave is gone reports POLLHUP but never drains. */
        if (pfd[2].revents & POLLOUT)
            flush_stdin();
        else if (pfd[2].revents)
            drop_stdin();
        if (pfd[1].revents)
            receive();

        if (pfd[0].revents) {
            while (read(s.sig_fd, &si, sizeof(si)) > 0)
                ;
            if (waitpid(s.pid, &status, WNOHANG) == s.pid)
                finish(status);
        }
    }
}

void session_run(int sock)
{
    struct open_req o = { 0 };
    unsigned char started[4];
    sigset_t mask;

    s.sock = sock;
    s.pid = -1;

    for (int sig = 1; sig < NSIG; sig++)
        signal(sig, SIG_DFL);
    signal(SIGPIPE, SIG_IGN);
    sigemptyset(&mask);
    sigaddset(&mask, SIGCHLD);
    sigprocmask(SIG_SETMASK, &mask, NULL);
    s.sig_fd = signalfd(-1, &mask, SFD_CLOEXEC | SFD_NONBLOCK);
    if (s.sig_fd < 0)
        _exit(1);

    read_open(&o);
    start_command(&o);

    put_le32(started, s.pid);
    send_frame(KERF_STARTED, started, sizeof(started));

    relay();
}
