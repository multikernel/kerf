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
 * Wire protocol between kerf exec on the host and kerf-init in a spawn.
 * Must match src/kerf/exec/protocol.py.
 */

#ifndef KERF_PROTO_H
#define KERF_PROTO_H

#include <stdint.h>

#define KERF_AGENT_PORT      1023
#define KERF_HDR_LEN         8
#define KERF_MAX_PAYLOAD     65536

#define KERF_OPEN            1
#define KERF_STDIN           2
#define KERF_STDIN_EOF       3
#define KERF_RESIZE          4
#define KERF_SIGNAL          5
#define KERF_STARTED         16
#define KERF_ERROR           17
#define KERF_STDOUT          18
#define KERF_STDERR          19
#define KERF_EXIT            20

#define KERF_PROTO_VERSION   1
#define KERF_OPEN_FIXED_LEN  24

#define KERF_OPEN_TTY        0x1
#define KERF_OPEN_STDIN      0x2
#define KERF_OPEN_USER       0x4

static inline uint16_t get_le16(const unsigned char *p)
{
    return (uint16_t)(p[0] | p[1] << 8);
}

static inline uint32_t get_le32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static inline void put_le16(unsigned char *p, uint16_t v)
{
    p[0] = v;
    p[1] = v >> 8;
}

static inline void put_le32(unsigned char *p, uint32_t v)
{
    p[0] = v;
    p[1] = v >> 8;
    p[2] = v >> 16;
    p[3] = v >> 24;
}

#endif
