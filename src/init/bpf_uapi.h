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

/*
 * The part of the kernel's BPF uapi that kerf-init uses. musl-gcc does not
 * see <linux/bpf.h>; names, values and layouts are copied from
 * include/uapi/linux/bpf.h and bpf_common.h.
 */

#ifndef KERF_INIT_BPF_UAPI_H
#define KERF_INIT_BPF_UAPI_H

#include <stdint.h>

#define BPF_LD    0x00
#define BPF_LDX   0x01
#define BPF_JMP   0x05
#define BPF_ALU64 0x07

#define BPF_W     0x00
#define BPF_DW    0x18
#define BPF_IMM   0x00
#define BPF_MEM   0x60

#define BPF_K     0x00
#define BPF_X     0x08

#define BPF_AND   0x50
#define BPF_RSH   0x70
#define BPF_MOV   0xb0

#define BPF_JEQ   0x10
#define BPF_JGT   0x20
#define BPF_JNE   0x50
#define BPF_EXIT  0x90

struct bpf_insn {
    uint8_t code;
    uint8_t dst_reg:4;
    uint8_t src_reg:4;
    int16_t off;
    int32_t imm;
};

/* enum bpf_cmd */
#define BPF_PROG_LOAD 5
#define BPF_LINK_CREATE 28

/* enum bpf_prog_type */
#define BPF_PROG_TYPE_LSM 29

/* enum bpf_attach_type */
#define BPF_LSM_MAC 27

#define BPF_OBJ_NAME_LEN 16

/*
 * union bpf_attr, cut down to the two commands used and to the leading
 * fields each needs. The kernel takes a shorter attr as zero-extended.
 */
union bpf_attr {
    struct {
        uint32_t prog_type;
        uint32_t insn_cnt;
        uint64_t insns;
        uint64_t license;
        uint32_t log_level;
        uint32_t log_size;
        uint64_t log_buf;
        uint32_t kern_version;
        uint32_t prog_flags;
        char prog_name[BPF_OBJ_NAME_LEN];
        uint32_t prog_ifindex;
        uint32_t expected_attach_type;
        uint32_t prog_btf_fd;
        uint32_t func_info_rec_size;
        uint64_t func_info;
        uint32_t func_info_cnt;
        uint32_t line_info_rec_size;
        uint64_t line_info;
        uint32_t line_info_cnt;
        uint32_t attach_btf_id;
    } prog_load;

    struct {
        uint32_t prog_fd;
        uint32_t target_fd;
        uint32_t attach_type;
        uint32_t flags;
    } link_create;
} __attribute__((aligned(8)));

#endif
