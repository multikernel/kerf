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

#ifndef KERF_INIT_BTF_H
#define KERF_INIT_BTF_H

#include <stddef.h>
#include <stdint.h>

struct btf {
    uint8_t *data;
    size_t len;
    const uint8_t *types;
    uint32_t types_len;
    const char *strs;
    uint32_t strs_len;
    const uint8_t *layout;
    uint32_t layout_len;
};

/* 0, -1 with errno set when the file cannot be read, -2 when it is not BTF */
int btf_load(const char *path, struct btf *btf);
void btf_free(struct btf *btf);

/* Type id of a function, or 0 */
uint32_t btf_find_func(const struct btf *btf, const char *name);

/* 0 and *value, or -1 when the enum or the enumerator is absent */
int btf_enum_value(const struct btf *btf, const char *enum_name,
                   const char *enumerator, int64_t *value);

/* Byte offset of a struct member, or -1 when absent or not on a byte */
int btf_member_offset(const struct btf *btf, const char *struct_name,
                      const char *member);

#endif
