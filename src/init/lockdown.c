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
 * Lockdown for the spawn: on top of the kernel's lockdown=integrity, deny
 * the confidentiality reasons named in kerf.deny= with a BPF LSM program.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

#include "lockdown.h"

#define BTF_PATH "/sys/kernel/btf/vmlinux"
#define LOCKDOWN_PATH "/sys/kernel/security/lockdown"
#define LSM_PATH "/sys/kernel/security/lsm"
#define LOCKDOWN_KEY "lockdown="
#define DENY_KEY "kerf.deny="
#define MAX_DENY_LEN 512

enum { LEVEL_NONE, LEVEL_INTEGRITY, LEVEL_CONFIDENTIALITY };

static const char *const level_names[] = { "none", "integrity", "confidentiality" };

/* PID 1 keeps these open for good: closing a link detaches its program */
static int deny_link = -1;
static int guard_link = -1;

#define REASON_ENUM "lockdown_reason"
#define REASON_PREFIX "LOCKDOWN_"
#define MAX_REASON_LEN 48
#define PTRACE_MODE_ATTACH 0x02

/* The enumerator a reason name stands for: xfrm-secret is LOCKDOWN_XFRM_SECRET */
static int reason_enumerator(const char *name, size_t len, char *out)
{
    if (!len || len > MAX_REASON_LEN)
        return -1;

    out += sprintf(out, REASON_PREFIX);
    for (size_t i = 0; i < len; i++) {
        char c = name[i];

        if (c >= 'a' && c <= 'z')
            *out++ = c - 'a' + 'A';
        else if (c >= '0' && c <= '9')
            *out++ = c;
        else if (c == '-')
            *out++ = '_';
        else
            return -1;
    }
    *out = '\0';
    return 0;
}

int lockdown_resolve(const struct btf *btf, const char *names,
                     struct deny_plan *plan, char *err, size_t errlen)
{
    int64_t integrity_max, confidentiality_max;
    int tgid_off;

    memset(plan, 0, sizeof(*plan));

    if (!*names) {
        snprintf(err, errlen, "kerf.deny= is empty");
        return -1;
    }

    plan->locked_down_id = btf_find_func(btf, "bpf_lsm_locked_down");
    plan->ptrace_id = btf_find_func(btf, "bpf_lsm_ptrace_access_check");
    if (!plan->locked_down_id || !plan->ptrace_id) {
        snprintf(err, errlen, "kernel has no BPF LSM (CONFIG_BPF_LSM)");
        return -1;
    }

    if (btf_enum_value(btf, REASON_ENUM, REASON_PREFIX "INTEGRITY_MAX",
                       &integrity_max) < 0 ||
        btf_enum_value(btf, REASON_ENUM, REASON_PREFIX "CONFIDENTIALITY_MAX",
                       &confidentiality_max) < 0) {
        snprintf(err, errlen, "kernel BTF lacks enum " REASON_ENUM);
        return -1;
    }

    tgid_off = btf_member_offset(btf, "task_struct", "tgid");
    if (tgid_off < 0) {
        snprintf(err, errlen, "kernel BTF lacks task_struct.tgid");
        return -1;
    }
    if (tgid_off > INT16_MAX) {
        snprintf(err, errlen, "task_struct.tgid is too far into the struct");
        return -1;
    }
    plan->tgid_off = tgid_off;

    for (const char *name = names; ; ) {
        char enumerator[sizeof(REASON_PREFIX) + MAX_REASON_LEN];
        size_t len = strcspn(name, ",");
        int64_t value;

        if (reason_enumerator(name, len, enumerator) < 0) {
            snprintf(err, errlen, "kerf.deny= has a malformed name");
            return -1;
        }
        if (btf_enum_value(btf, REASON_ENUM, enumerator, &value) < 0) {
            snprintf(err, errlen, "kernel has no lockdown reason '%.*s'",
                     (int)len, name);
            return -1;
        }
        /* An integrity reason left to this program alone could not be held */
        if (value <= integrity_max || value >= confidentiality_max) {
            snprintf(err, errlen,
                     "'%.*s' is not a confidentiality reason on this kernel",
                     (int)len, name);
            return -1;
        }
        if (value >= 64) {
            snprintf(err, errlen, "lockdown reason '%.*s' cannot be encoded",
                     (int)len, name);
            return -1;
        }
        plan->mask |= 1ULL << value;

        if (!name[len])
            break;
        name += len + 1;
    }
    return 0;
}

#define INSN(c, dst, src, o, i) \
    ((struct bpf_insn){ .code = (c), .dst_reg = (dst), .src_reg = (src), \
                        .off = (o), .imm = (i) })

/*
 * int locked_down(enum lockdown_reason what): refuse the reasons in the
 * mask. LSM arguments are 8-byte slots in the context r1 points at.
 */
void lockdown_emit_deny(const struct deny_plan *plan, struct bpf_insn *insns)
{
    int n = 0;

    insns[n++] = INSN(BPF_LDX | BPF_MEM | BPF_DW, 2, 1, 0, 0);
    insns[n++] = INSN(BPF_ALU64 | BPF_MOV | BPF_K, 0, 0, 0, 0);
    insns[n++] = INSN(BPF_JMP | BPF_JGT | BPF_K, 2, 0, 6, 63);
    insns[n++] = INSN(BPF_LD | BPF_IMM | BPF_DW, 3, 0, 0, (int32_t)(uint32_t)plan->mask);
    insns[n++] = INSN(0, 0, 0, 0, (int32_t)(uint32_t)(plan->mask >> 32));
    insns[n++] = INSN(BPF_ALU64 | BPF_RSH | BPF_X, 3, 2, 0, 0);
    insns[n++] = INSN(BPF_ALU64 | BPF_AND | BPF_K, 3, 0, 0, 1);
    insns[n++] = INSN(BPF_JMP | BPF_JEQ | BPF_K, 3, 0, 1, 0);
    insns[n++] = INSN(BPF_ALU64 | BPF_MOV | BPF_K, 0, 0, 0, -EPERM);
    insns[n++] = INSN(BPF_JMP | BPF_EXIT, 0, 0, 0, 0);
}

/*
 * int ptrace_access_check(struct task_struct *child, unsigned int mode):
 * refuse attaching to PID 1, which holds the links that keep both programs
 * alive. Reading /proc/1 is a different mode and stays allowed.
 */
void lockdown_emit_guard(const struct deny_plan *plan, struct bpf_insn *insns)
{
    int n = 0;

    insns[n++] = INSN(BPF_LDX | BPF_MEM | BPF_DW, 2, 1, 8, 0);
    insns[n++] = INSN(BPF_ALU64 | BPF_MOV | BPF_K, 0, 0, 0, 0);
    insns[n++] = INSN(BPF_ALU64 | BPF_AND | BPF_K, 2, 0, 0, PTRACE_MODE_ATTACH);
    insns[n++] = INSN(BPF_JMP | BPF_JEQ | BPF_K, 2, 0, 4, 0);
    insns[n++] = INSN(BPF_LDX | BPF_MEM | BPF_DW, 1, 1, 0, 0);
    insns[n++] = INSN(BPF_LDX | BPF_MEM | BPF_W, 1, 1, (int16_t)plan->tgid_off, 0);
    insns[n++] = INSN(BPF_JMP | BPF_JNE | BPF_K, 1, 0, 1, 1);
    insns[n++] = INSN(BPF_ALU64 | BPF_MOV | BPF_K, 0, 0, 0, -EPERM);
    insns[n++] = INSN(BPF_JMP | BPF_EXIT, 0, 0, 0, 0);
}

static int sys_bpf(int cmd, union bpf_attr *attr)
{
    return syscall(SYS_bpf, cmd, attr, sizeof(*attr));
}

/* The end of the verifier's log, on one line */
static void log_tail(char *vlog, char *out, size_t outlen)
{
    size_t len = strlen(vlog);
    char *start;

    while (len && vlog[len - 1] == '\n')
        vlog[--len] = '\0';
    start = len > outlen - 1 ? vlog + len - (outlen - 1) : vlog;
    snprintf(out, outlen, "%s", start);
    for (char *c = out; *c; c++) {
        if (*c == '\n')
            *c = ';';
    }
}

static int load_prog(const char *name, const struct bpf_insn *insns,
                     uint32_t count, uint32_t btf_id, char *err, size_t errlen)
{
    static char vlog[16384];
    union bpf_attr attr;
    char tail[160];
    int fd, saved;

    memset(&attr, 0, sizeof(attr));
    attr.prog_load.prog_type = BPF_PROG_TYPE_LSM;
    attr.prog_load.expected_attach_type = BPF_LSM_MAC;
    attr.prog_load.insns = (uintptr_t)insns;
    attr.prog_load.insn_cnt = count;
    attr.prog_load.license = (uintptr_t)"GPL";
    attr.prog_load.attach_btf_id = btf_id;
    snprintf(attr.prog_load.prog_name, sizeof(attr.prog_load.prog_name), "kerf_%s", name);

    fd = sys_bpf(BPF_PROG_LOAD, &attr);
    if (fd >= 0)
        return fd;
    saved = errno;

    /* Ask again for the verifier's reasons; a log on the first try could
     * itself fail the load by not fitting */
    vlog[0] = '\0';
    attr.prog_load.log_level = 1;
    attr.prog_load.log_buf = (uintptr_t)vlog;
    attr.prog_load.log_size = sizeof(vlog);
    fd = sys_bpf(BPF_PROG_LOAD, &attr);
    if (fd >= 0)
        return fd;

    log_tail(vlog, tail, sizeof(tail));
    snprintf(err, errlen, "cannot load %s program: %s%s%s", name, strerror(saved),
             tail[0] ? ": " : "", tail);
    return -1;
}

int lockdown_load(const struct deny_plan *plan, int *deny_fd, int *guard_fd,
                  char *err, size_t errlen)
{
    struct bpf_insn deny[DENY_PROG_INSNS], guard[GUARD_PROG_INSNS];

    lockdown_emit_deny(plan, deny);
    lockdown_emit_guard(plan, guard);

    *deny_fd = load_prog("deny", deny, DENY_PROG_INSNS, plan->locked_down_id, err, errlen);
    if (*deny_fd < 0)
        return -1;
    *guard_fd = load_prog("guard", guard, GUARD_PROG_INSNS, plan->ptrace_id, err, errlen);
    if (*guard_fd < 0) {
        close(*deny_fd);
        return -1;
    }
    return 0;
}

static int attach_prog(int prog_fd)
{
    union bpf_attr attr;
    int fd;

    memset(&attr, 0, sizeof(attr));
    attr.link_create.prog_fd = prog_fd;
    attr.link_create.attach_type = BPF_LSM_MAC;
    fd = sys_bpf(BPF_LINK_CREATE, &attr);
    if (fd >= 0)
        fcntl(fd, F_SETFD, FD_CLOEXEC);
    return fd;
}

static void attach_error(const char *name, char *err, size_t errlen)
{
    /* With the number: the kernel has errors here that libc cannot name */
    snprintf(err, errlen, "cannot attach %s program: error %d (%s)", name, errno,
             strerror(errno));
}

static int read_small(const char *path, char *buf, size_t size)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    ssize_t n;

    if (fd < 0)
        return -1;
    n = read(fd, buf, size - 1);
    close(fd);
    if (n < 0)
        return -1;
    buf[n] = '\0';
    return 0;
}

static int level_from(const char *name, size_t len)
{
    for (int level = LEVEL_INTEGRITY; level <= LEVEL_CONFIDENTIALITY; level++) {
        if (strlen(level_names[level]) == len && !strncmp(name, level_names[level], len))
            return level;
    }
    return LEVEL_NONE;
}

/*
 * What the command line asks for: the highest lockdown= level, as the
 * kernel only ever raises it, and the value of kerf.deny=. Returns how
 * many kerf.deny= words there are.
 */
static int parse_cmdline(const char *cmdline, int *level, const char **deny, size_t *deny_len)
{
    int count = 0, quoted = 0;
    const char *word = NULL;

    *level = LEVEL_NONE;
    for (const char *c = cmdline; ; c++) {
        int end = !*c || (!quoted && (*c == ' ' || *c == '\t' || *c == '\n'));

        if (!end) {
            if (*c == '"')
                quoted = !quoted;
            if (!word)
                word = c;
            continue;
        }
        if (word) {
            size_t len = c - word;

            if (len >= strlen(LOCKDOWN_KEY) && !strncmp(word, LOCKDOWN_KEY, strlen(LOCKDOWN_KEY))) {
                int asked = level_from(word + strlen(LOCKDOWN_KEY), len - strlen(LOCKDOWN_KEY));

                if (asked > *level)
                    *level = asked;
            } else if (len >= strlen(DENY_KEY) && !strncmp(word, DENY_KEY, strlen(DENY_KEY))) {
                *deny = word + strlen(DENY_KEY);
                *deny_len = len - strlen(DENY_KEY);
                count++;
            }
            word = NULL;
        }
        if (!*c)
            break;
    }
    return count;
}

/* The level the kernel is at, the bracketed word of its lockdown file */
static int active_level(void)
{
    char buf[128], *open_bracket, *close_bracket;

    if (read_small(LOCKDOWN_PATH, buf, sizeof(buf)) < 0)
        return -1;
    open_bracket = strchr(buf, '[');
    close_bracket = open_bracket ? strchr(open_bracket, ']') : NULL;
    if (!close_bracket)
        return -1;
    return level_from(open_bracket + 1, close_bracket - open_bracket - 1);
}

static int lsm_active(const char *lsm)
{
    char buf[512];
    size_t len = strlen(lsm);

    if (read_small(LSM_PATH, buf, sizeof(buf)) < 0)
        return 0;
    for (const char *entry = buf; ; ) {
        size_t entry_len = strcspn(entry, ",\n");

        if (entry_len == len && !strncmp(entry, lsm, len))
            return 1;
        if (!entry[entry_len])
            return 0;
        entry += entry_len + 1;
    }
}

static int setup_deny(const char *names, char *err, size_t errlen)
{
    struct deny_plan plan;
    int deny_fd, guard_fd;
    struct btf btf;
    int ret;

    if (!lsm_active("bpf")) {
        snprintf(err, errlen,
                 "BPF LSM is not active; add lsm=<list>,bpf to the spawn command line");
        return -1;
    }
    if (btf_load(BTF_PATH, &btf) < 0) {
        snprintf(err, errlen, "no usable kernel BTF (CONFIG_DEBUG_INFO_BTF)");
        return -1;
    }
    ret = lockdown_resolve(&btf, names, &plan, err, errlen);
    btf_free(&btf);
    if (ret < 0)
        return -1;

    if (lockdown_load(&plan, &deny_fd, &guard_fd, err, errlen) < 0)
        return -1;

    /* The guard first, so the deny list is never in force unprotected */
    guard_link = attach_prog(guard_fd);
    if (guard_link < 0) {
        attach_error("guard", err, errlen);
        ret = -1;
    } else {
        deny_link = attach_prog(deny_fd);
        if (deny_link < 0) {
            attach_error("deny", err, errlen);
            ret = -1;
        }
    }
    close(deny_fd);
    close(guard_fd);
    return ret;
}

int lockdown_setup(const char *cmdline, void (*log)(const char *msg))
{
    char names[MAX_DENY_LEN], err[256], msg[320 + MAX_DENY_LEN];
    const char *deny = NULL;
    size_t deny_len = 0;
    int asked, active, denies;

    denies = parse_cmdline(cmdline, &asked, &deny, &deny_len);
    if (asked == LEVEL_NONE && !denies)
        return 0;

    active = active_level();
    if (active < 0) {
        log("lockdown: cannot read " LOCKDOWN_PATH " (kernel built without the "
            "lockdown LSM, or lockdown missing from lsm=)");
        return -1;
    }
    if (active < asked) {
        snprintf(msg, sizeof(msg), "lockdown: kernel is at '%s', '%s' was requested",
                 level_names[active], level_names[asked]);
        log(msg);
        return -1;
    }

    if (denies) {
        if (denies > 1 || deny_len >= sizeof(names)) {
            log("lockdown: kerf.deny= is given more than once or is too long");
            return -1;
        }
        if (active < LEVEL_INTEGRITY) {
            log("lockdown: kerf.deny= needs lockdown=integrity");
            return -1;
        }
        memcpy(names, deny, deny_len);
        names[deny_len] = '\0';
        if (setup_deny(names, err, sizeof(err)) < 0) {
            snprintf(msg, sizeof(msg), "lockdown: %s", err);
            log(msg);
            return -1;
        }
        snprintf(msg, sizeof(msg), "lockdown: %s, deny %s", level_names[active], names);
    } else {
        snprintf(msg, sizeof(msg), "lockdown: %s", level_names[active]);
    }
    log(msg);
    return 0;
}
