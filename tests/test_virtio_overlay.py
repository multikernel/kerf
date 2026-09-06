import libfdt
from kerf.dtc.overlay import OverlayGenerator
from kerf.models import Instance, InstanceResources


def _instance(virtio):
    return Instance(name="web", id=1, resources=InstanceResources(
        cpus=[1], memory_base=0, memory_bytes=128 << 20, devices=[], virtio=virtio))


def test_instance_create_emits_virtio_nodes():
    dtbo = OverlayGenerator()._create_overlay_dtb({"web": _instance(["net"])}, {}, set())
    fdt = libfdt.Fdt(dtbo)
    node = fdt.path_offset("/fragment@0/__overlay__/instance-create/resources/virtio/net@0")
    assert fdt.getprop(node, "device-id").as_uint32() == 1
    assert fdt.getprop(node, "queues").as_uint32() == 2
    assert fdt.getprop(node, "queue-size").as_uint32() == 256


def test_no_virtio_no_node():
    dtbo = OverlayGenerator()._create_overlay_dtb({"web": _instance([])}, {}, set())
    fdt = libfdt.Fdt(dtbo)
    assert fdt.path_offset("/fragment@0/__overlay__/instance-create/resources/virtio",
                           quiet=[libfdt.FDT_ERR_NOTFOUND]) < 0
