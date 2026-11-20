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

#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "lockdown.h"

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
