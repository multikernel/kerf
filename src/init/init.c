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
 * Minimal init process for spawn kernel.
 *
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define CMDLINE_PATH "/proc/cmdline"
#define ENTRYPOINT_KEY "kerf.entrypoint="
#define CONSOLE_KEY "console="
#define MAX_CMDLINE_LEN 4096
#define MAX_ENTRYPOINT_LEN 4096
#define MAX_CONSOLE_LEN 64
#define MAX_ARGS 64

static volatile pid_t child_pid = -1;
static volatile int child_exited = 0;
static volatile int child_exit_status = 0;
static char console_device[MAX_CONSOLE_LEN];

static void log_msg(const char *msg)
{
    int fd = open("/dev/kmsg", O_WRONLY);
    if (fd >= 0) {
        write(fd, "kerf-init: ", 11);
        write(fd, msg, strlen(msg));
        write(fd, "\n", 1);
        close(fd);
    }
}

static void log_error(const char *msg)
{
    char buf[256];
    snprintf(buf, sizeof(buf), "ERROR: %s: %s", msg, strerror(errno));
    log_msg(buf);
}

static void log_starting(void)
{
    struct timespec ts;
    struct tm tm;
    char buf[80];

    if (clock_gettime(CLOCK_REALTIME, &ts) < 0 ||
        gmtime_r(&ts.tv_sec, &tm) == NULL) {
        log_msg("starting");
        return;
    }

    snprintf(buf, sizeof(buf), "starting at %04d-%02d-%02d %02d:%02d:%02d.%03ld UTC",
             tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
             tm.tm_hour, tm.tm_min, tm.tm_sec,
             ts.tv_nsec / 1000000);
    log_msg(buf);
}

static int do_mount(const char *source, const char *target,
                    const char *fstype, unsigned long flags)
{
    if (mount(source, target, fstype, flags, NULL) < 0) {
        if (errno != EBUSY) {
            log_error(target);
            return -1;
        }
    }
    return 0;
}

static int do_mkdir(const char *path, mode_t mode)
{
    if (mkdir(path, mode) < 0) {
        if (errno != EEXIST) {
            log_error(path);
            return -1;
        }
    }
    return 0;
}

static int mount_filesystems(void)
{
    if (do_mount("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC) < 0)
        return -1;

    if (do_mount("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC) < 0)
        return -1;

    /* Mount devtmpfs to populate /dev with kernel device nodes */
    if (do_mount("devtmpfs", "/dev", "devtmpfs", MS_NOSUID) < 0)
        return -1;

    if (do_mkdir("/dev/pts", 0755) < 0)
        return -1;

    if (do_mount("devpts", "/dev/pts", "devpts", MS_NOSUID | MS_NOEXEC) < 0)
        return -1;

    return 0;
}

static int read_entrypoint(char *buf, size_t bufsize)
{
    char cmdline[MAX_CMDLINE_LEN];
    int fd = open(CMDLINE_PATH, O_RDONLY);
    if (fd < 0) {
        log_error("open " CMDLINE_PATH);
        return -1;
    }

    ssize_t n = read(fd, cmdline, sizeof(cmdline) - 1);
    close(fd);

    if (n < 0) {
        log_error("read " CMDLINE_PATH);
        return -1;
    }

    cmdline[n] = '\0';

    /* Find kerf.entrypoint= in cmdline */
    char *start = strstr(cmdline, ENTRYPOINT_KEY);
    if (!start) {
        log_msg("kerf.entrypoint= not found in cmdline");
        return -1;
    }

    start += strlen(ENTRYPOINT_KEY);

    char *end;
    if (*start == '"') {
        /* Quoted value: find closing quote */
        start++;  /* Skip opening quote */
        end = strchr(start, '"');
        if (!end) {
            log_msg("unterminated quote in kerf.entrypoint");
            return -1;
        }
    } else {
        /* Unquoted value: find space or end of string */
        end = start;
        while (*end && *end != ' ' && *end != '\n')
            end++;
    }

    size_t len = end - start;
    if (len == 0) {
        log_msg("empty kerf.entrypoint value");
        return -1;
    }

    if (len >= bufsize) {
        log_msg("kerf.entrypoint value too long");
        return -1;
    }

    memcpy(buf, start, len);
    buf[len] = '\0';

    return 0;
}

static int read_console(char *buf, size_t bufsize)
{
    char cmdline[MAX_CMDLINE_LEN];
    int fd = open(CMDLINE_PATH, O_RDONLY);
    if (fd < 0)
        return -1;

    ssize_t n = read(fd, cmdline, sizeof(cmdline) - 1);
    close(fd);

    if (n < 0)
        return -1;

    cmdline[n] = '\0';

    /* Find console= in cmdline */
    char *start = strstr(cmdline, CONSOLE_KEY);
    if (!start)
        return -1;

    start += strlen(CONSOLE_KEY);

    /* Find the end of the value (space, comma, or end of string) */
    char *end = start;
    while (*end && *end != ' ' && *end != ',' && *end != '\n')
        end++;

    size_t len = end - start;
    if (len == 0 || len >= bufsize)
        return -1;

    /* Build /dev/<console> path */
    if (len + 5 >= bufsize)  /* "/dev/" is 5 chars */
        return -1;

    snprintf(buf, bufsize, "/dev/%.*s", (int)len, start);

    return 0;
}

static void setup_console(const char *tty)
{
    int fd;
    struct termios term;

    /* Create new session (detach from current terminal) */
    setsid();

    /* Open the TTY */
    fd = open(tty, O_RDWR | O_NOCTTY);
    if (fd < 0) {
        log_error(tty);
        return;
    }

    /* Make it the controlling terminal */
    ioctl(fd, TIOCSCTTY, 1);

    /* Set up termios */
    if (tcgetattr(fd, &term) == 0) {
        term.c_iflag = ICRNL | IXON;
        term.c_oflag = OPOST | ONLCR;
        term.c_cflag = B115200 | CS8 | CREAD | HUPCL | CLOCAL;
        term.c_lflag = ISIG | ICANON | ECHO | ECHOE | ECHOK;
        tcsetattr(fd, TCSANOW, &term);
    }

    /* Redirect stdin/stdout/stderr */
    dup2(fd, STDIN_FILENO);
    dup2(fd, STDOUT_FILENO);
    dup2(fd, STDERR_FILENO);
    if (fd > STDERR_FILENO)
        close(fd);
}

static int parse_args(char *cmdline, char **argv, int max_args)
{
    int argc = 0;
    char *p = cmdline;
    int in_quote = 0;
    char quote_char = 0;

    while (*p && argc < max_args - 1) {
        /* Skip whitespace */
        while (*p == ' ' || *p == '\t')
            p++;

        if (*p == '\0')
            break;

        argv[argc++] = p;

        /* Find end of argument */
        while (*p) {
            if (!in_quote) {
                if (*p == '"' || *p == '\'') {
                    in_quote = 1;
                    quote_char = *p;
                    memmove(p, p + 1, strlen(p));
                    continue;
                }
                if (*p == ' ' || *p == '\t') {
                    *p++ = '\0';
                    break;
                }
            } else {
                if (*p == quote_char) {
                    in_quote = 0;
                    memmove(p, p + 1, strlen(p));
                    continue;
                }
            }
            p++;
        }
    }

    argv[argc] = NULL;
    return argc;
}

static void sigchld_handler(int sig)
{
    (void)sig;
    int status;
    pid_t pid;

    while ((pid = waitpid(-1, &status, WNOHANG)) > 0) {
        if (pid == child_pid) {
            child_exited = 1;
            if (WIFEXITED(status)) {
                child_exit_status = WEXITSTATUS(status);
            } else if (WIFSIGNALED(status)) {
                child_exit_status = 128 + WTERMSIG(status);
            }
        }
    }
}

static void forward_signal(int sig)
{
    if (child_pid > 0) {
        kill(child_pid, sig);
    }
}

static void setup_signals(void)
{
    struct sigaction sa;

    /* Handle SIGCHLD to reap zombies */
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = sigchld_handler;
    sa.sa_flags = SA_RESTART | SA_NOCLDSTOP;
    sigaction(SIGCHLD, &sa, NULL);

    /* Forward termination signals to child */
    sa.sa_handler = forward_signal;
    sa.sa_flags = SA_RESTART;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
}

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    char entrypoint[MAX_ENTRYPOINT_LEN];
    char *ep_argv[MAX_ARGS];

    log_starting();

    if (mount_filesystems() < 0) {
        log_msg("failed to mount filesystems");
        return 1;
    }

    if (read_entrypoint(entrypoint, sizeof(entrypoint)) < 0) {
        log_msg("failed to read entrypoint");
        return 1;
    }

    {
        char msg[256];
        snprintf(msg, sizeof(msg), "entrypoint: '%.200s'", entrypoint);
        log_msg(msg);
    }

    /* Read console device (optional) */
    if (read_console(console_device, sizeof(console_device)) == 0) {
        char msg[128];
        snprintf(msg, sizeof(msg), "console: %s", console_device);
        log_msg(msg);
    } else {
        console_device[0] = '\0';
    }

    int ep_argc = parse_args(entrypoint, ep_argv, MAX_ARGS);
    if (ep_argc == 0) {
        log_msg("no entrypoint arguments");
        return 1;
    }

    {
        char msg[512];
        int off = snprintf(msg, sizeof(msg), "executing:");
        for (int i = 0; i < ep_argc && off < (int)sizeof(msg) - 1; i++)
            off += snprintf(msg + off, sizeof(msg) - off, " %s", ep_argv[i]);
        log_msg(msg);
    }

    setup_signals();

    child_pid = fork();
    if (child_pid < 0) {
        log_error("fork");
        return 1;
    }

    if (child_pid == 0) {
        /* Child process */
        if (console_device[0] != '\0')
            setup_console(console_device);

        execv(ep_argv[0], ep_argv);
        log_error("execv");
        _exit(127);
    }

    /* Parent process - stay as PID 1 forever.
     * PID 1 must never exit or the kernel will panic.
     * Keep reaping zombies and waiting for signals.
     */
    for (;;) {
        pause();
        if (child_exited) {
            char msg[64];
            snprintf(msg, sizeof(msg), "child exited with status %d",
                     child_exit_status);
            log_msg(msg);
            child_exited = 0;  /* Reset for any future children */
        }
    }

    /* Never reached */
    return 0;
}
