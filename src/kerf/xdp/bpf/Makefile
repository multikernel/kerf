# Builds the XDP object kerf ships in its wheel (see kerf_build.py). Needs
# clang with BPF support and the libbpf headers.
CLANG ?= clang
# -g emits the BTF that libbpf 1.x requires to create maps declared in the
# .maps section.
CFLAGS := -O2 -g -target bpf -Wall

# With -target bpf clang skips the multiarch include directory that Debian
# and Ubuntu keep asm/ headers in; Fedora and Alpine have no such directory.
MULTIARCH := $(shell $(CC) -print-multiarch 2>/dev/null)
ifneq ($(MULTIARCH),)
CFLAGS += -idirafter /usr/include/$(MULTIARCH)
endif

all: mk_xdp.o

mk_xdp.o: mk_xdp.c
	$(CLANG) $(CFLAGS) -c $< -o $@

clean:
	rm -f mk_xdp.o mk_xdp_nic.o mk_xdp_vnet.o
