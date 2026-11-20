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
 */

#ifndef KERF_INIT_LOCKDOWN_H
#define KERF_INIT_LOCKDOWN_H

#include <stddef.h>
#include <stdint.h>

#include "bpf_uapi.h"
#include "btf.h"

struct deny_plan {
    uint64_t mask;
    uint32_t locked_down_id;
    uint32_t ptrace_id;
    uint32_t tgid_off;
};

#define DENY_PROG_INSNS  10
#define GUARD_PROG_INSNS 9

/*
 * Turn the value of kerf.deny= into what the two programs need from this
 * kernel. Returns 0, or -1 with a message for the log in err.
 */
int lockdown_resolve(const struct btf *btf, const char *names,
                     struct deny_plan *plan, char *err, size_t errlen);

void lockdown_emit_deny(const struct deny_plan *plan, struct bpf_insn *insns);
void lockdown_emit_guard(const struct deny_plan *plan, struct bpf_insn *insns);

/*
 * Load both programs into the running kernel without attaching them.
 * Returns 0 with the program fds, or -1 with a message for the log in err.
 */
int lockdown_load(const struct deny_plan *plan, int *deny_fd, int *guard_fd,
                  char *err, size_t errlen);

/*
 * Put the lockdown the command line asks for in force: verify the level
 * the kernel was told, and attach the kerf.deny= programs. Returns 0 when
 * nothing was asked or all of it holds, -1 after logging why not.
 */
int lockdown_setup(const char *cmdline, void (*log)(const char *msg));

#endif
