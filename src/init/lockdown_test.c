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
 * Runs kerf-init's lockdown code on a BTF file for the tests; not shipped.
 */

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "btf.h"
#include "lockdown.h"

static int test_btf(const struct btf *btf)
{
    int64_t kcore;

    printf("bpf_lsm_locked_down=%u\n", btf_find_func(btf, "bpf_lsm_locked_down"));
    printf("bpf_lsm_ptrace_access_check=%u\n",
           btf_find_func(btf, "bpf_lsm_ptrace_access_check"));
    printf("task_struct.tgid=%d\n", btf_member_offset(btf, "task_struct", "tgid"));
    if (btf_enum_value(btf, "lockdown_reason", "LOCKDOWN_KCORE", &kcore) < 0)
        printf("LOCKDOWN_KCORE=absent\n");
    else
        printf("LOCKDOWN_KCORE=%" PRId64 "\n", kcore);
    return 0;
}

static void print_insns(const char *prog, const struct bpf_insn *insns, int count)
{
    for (int i = 0; i < count; i++)
        printf("%s %02x %u %u %d %d\n", prog, insns[i].code, insns[i].dst_reg,
               insns[i].src_reg, insns[i].off, insns[i].imm);
}

static int test_plan(const struct btf *btf, const char *names)
{
    struct bpf_insn deny[DENY_PROG_INSNS], guard[GUARD_PROG_INSNS];
    struct deny_plan plan;
    char err[128];

    if (lockdown_resolve(btf, names, &plan, err, sizeof(err)) < 0) {
        fprintf(stderr, "error: %s\n", err);
        return 1;
    }

    printf("mask=%" PRIx64 "\n", plan.mask);
    printf("locked_down_id=%u\n", plan.locked_down_id);
    printf("ptrace_id=%u\n", plan.ptrace_id);
    printf("tgid_off=%u\n", plan.tgid_off);
    lockdown_emit_deny(&plan, deny);
    lockdown_emit_guard(&plan, guard);
    print_insns("deny", deny, DENY_PROG_INSNS);
    print_insns("guard", guard, GUARD_PROG_INSNS);
    return 0;
}

int main(int argc, char *argv[])
{
    int plan = argc == 4 && !strcmp(argv[1], "plan");
    struct btf btf;
    int ret;

    if (!plan && (argc != 3 || strcmp(argv[1], "btf"))) {
        fprintf(stderr, "usage: lockdown-test btf BTF_FILE\n"
                        "       lockdown-test plan BTF_FILE NAMES\n");
        return 2;
    }

    ret = btf_load(argv[2], &btf);
    if (ret == -1) {
        fprintf(stderr, "error: %s: %s\n", argv[2], strerror(errno));
        return 1;
    }
    if (ret < 0) {
        fprintf(stderr, "error: %s: not valid BTF\n", argv[2]);
        return 1;
    }

    ret = plan ? test_plan(&btf, argv[3]) : test_btf(&btf);
    btf_free(&btf);
    return ret;
}
