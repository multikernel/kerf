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
 * Just enough of a BTF reader to find what kerf-init needs in
 * /sys/kernel/btf/vmlinux. musl-gcc does not see the kernel's uapi
 * headers, so the format is spelled out here; see include/uapi/linux/btf.h.
 */

#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "btf.h"

#define BTF_MAGIC 0xeB9F
#define BTF_HDR_MIN 24
#define BTF_HDR_LAYOUT 32
#define BTF_TYPE_SIZE 12
#define BTF_MAX_SIZE (256u << 20)

enum {
    BTF_KIND_INT = 1,
    BTF_KIND_PTR,
    BTF_KIND_ARRAY,
    BTF_KIND_STRUCT,
    BTF_KIND_UNION,
    BTF_KIND_ENUM,
    BTF_KIND_FWD,
    BTF_KIND_TYPEDEF,
    BTF_KIND_VOLATILE,
    BTF_KIND_CONST,
    BTF_KIND_RESTRICT,
    BTF_KIND_FUNC,
    BTF_KIND_FUNC_PROTO,
    BTF_KIND_VAR,
    BTF_KIND_DATASEC,
    BTF_KIND_FLOAT,
    BTF_KIND_DECL_TAG,
    BTF_KIND_TYPE_TAG,
    BTF_KIND_ENUM64,
    BTF_KIND_KNOWN,
};

struct kind_layout {
    uint8_t info_sz;
    uint8_t elem_sz;
};

static const struct kind_layout known_layout[BTF_KIND_KNOWN] = {
    [BTF_KIND_INT] = { 4, 0 },
    [BTF_KIND_ARRAY] = { 12, 0 },
    [BTF_KIND_STRUCT] = { 0, 12 },
    [BTF_KIND_UNION] = { 0, 12 },
    [BTF_KIND_ENUM] = { 0, 8 },
    [BTF_KIND_FUNC_PROTO] = { 0, 8 },
    [BTF_KIND_VAR] = { 4, 0 },
    [BTF_KIND_DATASEC] = { 0, 12 },
    [BTF_KIND_DECL_TAG] = { 4, 0 },
    [BTF_KIND_ENUM64] = { 0, 12 },
};

struct btf_rec {
    uint32_t id;
    uint32_t kind;
    uint32_t vlen;
    int kflag;
    const char *name;
    const uint8_t *elems;
};

static uint32_t rd32(const uint8_t *p)
{
    uint32_t v;

    memcpy(&v, p, sizeof(v));
    return v;
}

/* The string section is checked to end in a NUL, so any offset in it is safe */
static const char *btf_str(const struct btf *btf, uint32_t off)
{
    return off < btf->strs_len ? btf->strs + off : NULL;
}

static int kind_layout(const struct btf *btf, uint32_t kind, struct kind_layout *kl)
{
    if (!kind)
        return -1;

    /* A newer kernel describes its kinds itself, including ones unknown here */
    if (btf->layout_len) {
        if ((uint64_t)kind * 4 + 4 > btf->layout_len)
            return -1;
        kl->info_sz = btf->layout[kind * 4];
        kl->elem_sz = btf->layout[kind * 4 + 1];
        return 0;
    }

    if (kind >= BTF_KIND_KNOWN)
        return -1;
    *kl = known_layout[kind];
    return 0;
}

/*
 * Step to the next type record. Returns 1 with *rec filled in, 0 at the
 * end, -1 when the record does not fit or cannot be sized.
 */
static int btf_next(const struct btf *btf, uint32_t *off, struct btf_rec *rec)
{
    struct kind_layout kl;
    const uint8_t *t = btf->types + *off;
    uint32_t info;
    uint64_t size;

    if (*off == btf->types_len)
        return 0;
    if (btf->types_len - *off < BTF_TYPE_SIZE)
        return -1;

    info = rd32(t + 4);
    rec->kind = (info >> 24) & 0x1f;
    rec->vlen = info & 0xffff;
    rec->kflag = info >> 31;
    if (kind_layout(btf, rec->kind, &kl) < 0)
        return -1;

    size = BTF_TYPE_SIZE + kl.info_sz + (uint64_t)rec->vlen * kl.elem_sz;
    if (size > btf->types_len - *off)
        return -1;

    rec->name = btf_str(btf, rd32(t));
    if (!rec->name)
        return -1;
    rec->elems = t + BTF_TYPE_SIZE + kl.info_sz;
    rec->id++;
    *off += size;
    return 1;
}

static int read_file(const char *path, uint8_t **data, size_t *len)
{
    size_t cap = 1 << 20, used = 0;
    uint8_t *buf = malloc(cap);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    ssize_t n;

    if (fd < 0 || !buf)
        goto fail;

    for (;;) {
        if (used == cap) {
            uint8_t *bigger;

            if (cap >= BTF_MAX_SIZE) {
                errno = EFBIG;
                goto fail;
            }
            cap *= 2;
            bigger = realloc(buf, cap);
            if (!bigger)
                goto fail;
            buf = bigger;
        }
        n = read(fd, buf + used, cap - used);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            goto fail;
        }
        if (n == 0)
            break;
        used += n;
    }

    close(fd);
    *data = buf;
    *len = used;
    return 0;

fail:
    {
        int saved = errno;

        if (fd >= 0)
            close(fd);
        free(buf);
        errno = saved;
    }
    return -1;
}

static int section(const struct btf *btf, uint32_t hdr_len, uint32_t off,
                   uint32_t len, const uint8_t **start)
{
    if ((uint64_t)hdr_len + off + len > btf->len)
        return -1;
    *start = btf->data + hdr_len + off;
    return 0;
}

/* The kinds this file reads into must be laid out the way it reads them */
static int layout_agrees(const struct btf *btf)
{
    static const uint32_t read_kinds[] = {
        BTF_KIND_STRUCT, BTF_KIND_ENUM, BTF_KIND_FUNC, BTF_KIND_ENUM64,
    };

    for (size_t i = 0; i < sizeof(read_kinds) / sizeof(read_kinds[0]); i++) {
        struct kind_layout kl;

        if (kind_layout(btf, read_kinds[i], &kl) < 0 ||
            kl.info_sz != known_layout[read_kinds[i]].info_sz ||
            kl.elem_sz != known_layout[read_kinds[i]].elem_sz)
            return 0;
    }
    return 1;
}

static int btf_parse(struct btf *btf)
{
    const uint8_t *hdr = btf->data, *strs;
    struct btf_rec rec = { 0 };
    uint32_t hdr_len, off = 0;
    uint16_t magic;
    int ret;

    if (btf->len < BTF_HDR_MIN)
        return -1;
    memcpy(&magic, hdr, sizeof(magic));
    hdr_len = rd32(hdr + 4);
    if (magic != BTF_MAGIC || hdr_len < BTF_HDR_MIN || hdr_len > btf->len)
        return -1;

    btf->types_len = rd32(hdr + 12);
    btf->strs_len = rd32(hdr + 20);
    if (section(btf, hdr_len, rd32(hdr + 8), btf->types_len, &btf->types) < 0 ||
        section(btf, hdr_len, rd32(hdr + 16), btf->strs_len, &strs) < 0)
        return -1;
    if (!btf->strs_len || strs[btf->strs_len - 1] != '\0')
        return -1;
    btf->strs = (const char *)strs;

    btf->layout_len = 0;
    if (hdr_len >= BTF_HDR_LAYOUT) {
        btf->layout_len = rd32(hdr + 28);
        if (section(btf, hdr_len, rd32(hdr + 24), btf->layout_len, &btf->layout) < 0)
            return -1;
    }
    if (!layout_agrees(btf))
        return -1;

    /* Walk once here so that the lookups can trust every record */
    while ((ret = btf_next(btf, &off, &rec)) > 0)
        ;
    return ret;
}

int btf_load(const char *path, struct btf *btf)
{
    memset(btf, 0, sizeof(*btf));
    if (read_file(path, &btf->data, &btf->len) < 0)
        return -1;
    if (btf_parse(btf) < 0) {
        btf_free(btf);
        return -2;
    }
    return 0;
}

void btf_free(struct btf *btf)
{
    free(btf->data);
    memset(btf, 0, sizeof(*btf));
}

static int name_is(const struct btf *btf, uint32_t off, const char *name)
{
    const char *s = btf_str(btf, off);

    return s && !strcmp(s, name);
}

uint32_t btf_find_func(const struct btf *btf, const char *name)
{
    struct btf_rec rec = { 0 };
    uint32_t off = 0;

    while (btf_next(btf, &off, &rec) > 0) {
        if (rec.kind == BTF_KIND_FUNC && !strcmp(rec.name, name))
            return rec.id;
    }
    return 0;
}

int btf_enum_value(const struct btf *btf, const char *enum_name,
                   const char *enumerator, int64_t *value)
{
    struct btf_rec rec = { 0 };
    uint32_t off = 0;

    while (btf_next(btf, &off, &rec) > 0) {
        int wide = rec.kind == BTF_KIND_ENUM64;

        if ((rec.kind != BTF_KIND_ENUM && !wide) || strcmp(rec.name, enum_name))
            continue;

        for (uint32_t i = 0; i < rec.vlen; i++) {
            const uint8_t *e = rec.elems + i * (wide ? 12 : 8);

            if (!name_is(btf, rd32(e), enumerator))
                continue;
            if (wide)
                *value = (int64_t)(rd32(e + 4) | (uint64_t)rd32(e + 8) << 32);
            else
                *value = (int32_t)rd32(e + 4);
            return 0;
        }
    }
    return -1;
}

int btf_member_offset(const struct btf *btf, const char *struct_name,
                      const char *member)
{
    struct btf_rec rec = { 0 };
    uint32_t off = 0;

    while (btf_next(btf, &off, &rec) > 0) {
        if (rec.kind != BTF_KIND_STRUCT || strcmp(rec.name, struct_name))
            continue;

        for (uint32_t i = 0; i < rec.vlen; i++) {
            const uint8_t *m = rec.elems + i * 12;
            uint32_t bits = rd32(m + 8);

            if (!name_is(btf, rd32(m), member))
                continue;
            /* With kflag, the top byte is a bitfield size */
            if (rec.kflag) {
                if (bits >> 24)
                    return -1;
                bits &= 0xffffff;
            }
            if (bits % 8 || bits / 8 > INT32_MAX)
                return -1;
            return bits / 8;
        }
    }
    return -1;
}
