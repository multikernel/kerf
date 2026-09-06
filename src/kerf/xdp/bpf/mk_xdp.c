// SPDX-License-Identifier: GPL-2.0
/*
 * XDP fast path between a NIC and the mk_vnet netdevs of app-kernels,
 * with the app-kernels hidden behind the NIC's own MAC address.
 *
 * mk_xdp_vnet runs on each app-kernel's netdev: it learns the app-kernel's
 * IPv4 addresses from what it sends, rewrites the source MAC (and the ARP
 * sender address) to the NIC's, and redirects to the NIC.
 *
 * mk_xdp_nic runs on the NIC: IPv4 and ARP frames whose destination or
 * target address belongs to an app-kernel get the app-kernel's MAC written
 * back in and are redirected to its netdev; everything else passes to the
 * host. The switch and routers only ever see the NIC's MAC, so a port that
 * allows one address per host, or a NIC the host keeps using itself, both
 * work. IPv4 only: IPv6 neighbor discovery carries the link-layer address
 * under an ICMPv6 checksum and is left to a later version.
 *
 * One object serves one NIC; kerf attaches mk_xdp_nic to the NIC and
 * mk_xdp_vnet to every netdev routed through it.
 */
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <bpf/bpf_endian.h>
#include <bpf/bpf_helpers.h>

/*
 * linux/if_arp.h reaches glibc through linux/netdevice.h, and glibc's
 * headers do not build for the BPF target; the ARP header is small enough
 * to carry here.
 */
#define MK_ARPOP_REPLY	2

struct arphdr_ipv4 {
	__be16 ar_hrd;
	__be16 ar_pro;
	__u8 ar_hln;
	__u8 ar_pln;
	__be16 ar_op;
};

struct mk_spawn {
	__u32 ifindex;
	__u8 mac[ETH_ALEN];
	__u8 pad[2];
};

struct mk_uplink {
	__u32 ifindex;
	__u8 mac[ETH_ALEN];
	__u8 pad[2];
};

struct arp_ipv4 {
	struct arphdr_ipv4 hdr;
	__u8 sha[ETH_ALEN];
	__u8 sip[4];
	__u8 tha[ETH_ALEN];
	__u8 tip[4];
} __attribute__((packed));

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__type(key, __u32);		/* IPv4 address, network order */
	__type(value, struct mk_spawn);
	__uint(max_entries, 256);
} spawn_ips SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__type(key, __u32);
	__type(value, struct mk_uplink);
	__uint(max_entries, 1);
} uplink SEC(".maps");

static __always_inline void mac_copy(__u8 *dst, const __u8 *src)
{
	__builtin_memcpy(dst, src, ETH_ALEN);
}

static __always_inline __u32 ipv4_key(const __u8 *addr)
{
	__u32 key;

	__builtin_memcpy(&key, addr, 4);
	return key;
}

static __always_inline void learn(__u32 ip, __u32 ifindex, const __u8 *mac)
{
	struct mk_spawn *known = bpf_map_lookup_elem(&spawn_ips, &ip);
	struct mk_spawn entry = { .ifindex = ifindex };

	if (!ip)
		return;
	if (known && known->ifindex == ifindex &&
	    !__builtin_memcmp(known->mac, mac, ETH_ALEN))
		return;
	mac_copy(entry.mac, mac);
	bpf_map_update_elem(&spawn_ips, &ip, &entry, BPF_ANY);
}

SEC("xdp")
int mk_xdp_vnet(struct xdp_md *ctx)
{
	void *data = (void *)(long)ctx->data;
	void *data_end = (void *)(long)ctx->data_end;
	struct ethhdr *eth = data;
	__u32 zero = 0;
	struct mk_uplink *up = bpf_map_lookup_elem(&uplink, &zero);

	if (!up || !up->ifindex)
		return XDP_PASS;
	if ((void *)(eth + 1) > data_end)
		return XDP_PASS;

	if (eth->h_proto == bpf_htons(ETH_P_IP)) {
		struct iphdr *ip = (void *)(eth + 1);

		if ((void *)(ip + 1) > data_end)
			return XDP_DROP;
		learn(ip->saddr, ctx->ingress_ifindex, eth->h_source);
	} else if (eth->h_proto == bpf_htons(ETH_P_ARP)) {
		struct arp_ipv4 *arp = (void *)(eth + 1);

		if ((void *)(arp + 1) > data_end)
			return XDP_DROP;
		learn(ipv4_key(arp->sip), ctx->ingress_ifindex, arp->sha);
		mac_copy(arp->sha, up->mac);
	}
	mac_copy(eth->h_source, up->mac);
	return bpf_redirect(up->ifindex, 0);
}

SEC("xdp")
int mk_xdp_nic(struct xdp_md *ctx)
{
	void *data = (void *)(long)ctx->data;
	void *data_end = (void *)(long)ctx->data_end;
	struct ethhdr *eth = data;
	struct mk_spawn *spawn;
	__u32 key;

	if ((void *)(eth + 1) > data_end)
		return XDP_PASS;

	if (eth->h_proto == bpf_htons(ETH_P_IP)) {
		struct iphdr *ip = (void *)(eth + 1);

		if ((void *)(ip + 1) > data_end)
			return XDP_PASS;
		spawn = bpf_map_lookup_elem(&spawn_ips, &ip->daddr);
		if (!spawn)
			return XDP_PASS;
		mac_copy(eth->h_dest, spawn->mac);
		return bpf_redirect(spawn->ifindex, 0);
	}
	if (eth->h_proto == bpf_htons(ETH_P_ARP)) {
		struct arp_ipv4 *arp = (void *)(eth + 1);

		if ((void *)(arp + 1) > data_end)
			return XDP_PASS;
		key = ipv4_key(arp->tip);
		spawn = bpf_map_lookup_elem(&spawn_ips, &key);
		if (!spawn)
			return XDP_PASS;
		/* A reply names the target's hardware address; a request leaves it to us */
		if (arp->hdr.ar_op == bpf_htons(MK_ARPOP_REPLY)) {
			mac_copy(arp->tha, spawn->mac);
			mac_copy(eth->h_dest, spawn->mac);
		}
		return bpf_redirect(spawn->ifindex, 0);
	}
	return XDP_PASS;
}

char _license[] SEC("license") = "GPL";
