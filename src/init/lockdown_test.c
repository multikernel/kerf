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

int main(int argc, char *argv[])
{
    struct btf btf;
    int ret;

    if (argc < 3 || strcmp(argv[1], "btf")) {
        fprintf(stderr, "usage: lockdown-test btf BTF_FILE\n");
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

    ret = test_btf(&btf);
    btf_free(&btf);
    return ret;
}
