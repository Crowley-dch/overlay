from __future__ import annotations
from dataclasses import dataclass, field
from overlay.dht.contact import Contact

class BootstrapError(Exception):
    pass

@dataclass
class BootstrapPlan:
    scheme: str
    n_nodes: int
    seeds: dict[str, list[Contact]] = field(default_factory=dict)
    launch_order: list[str] = field(default_factory=list)

    def seeds_for(self, node_id_hex: str) -> list[Contact]:
        return self.seeds.get(node_id_hex, [])

    def describe(self) -> dict:
        return {
            "scheme": self.scheme,
            "n_nodes": self.n_nodes,
            "launch_order": list(self.launch_order),
            "seeds": {
                node_id: [c.node_id_hex for c in contacts]
                for node_id, contacts in self.seeds.items()
            },
        }


def build_star(contacts: list[Contact]) -> BootstrapPlan:
    if len(contacts) < 2:
        raise BootstrapError("star требует не менее 2 узлов")
    seed = contacts[0]
    seeds: dict[str, list[Contact]] = {}
    launch_order: list[str] = [seed.node_id_hex]

    seeds[seed.node_id_hex] = []
    for c in contacts[1:]:
        seeds[c.node_id_hex] = [seed]
        launch_order.append(c.node_id_hex)

    return BootstrapPlan(
        scheme="star",
        n_nodes=len(contacts),
        seeds=seeds,
        launch_order=launch_order,
    )


def build_ring(contacts: list[Contact]) -> BootstrapPlan:
    if len(contacts) < 3:
        raise BootstrapError("ring требует не менее 3 узлов")
    n = len(contacts)
    seeds: dict[str, list[Contact]] = {}
    launch_order: list[str] = []

    for i, c in enumerate(contacts):
        successor = contacts[(i + 1) % n]
        seeds[c.node_id_hex] = [successor]
        launch_order.append(c.node_id_hex)

    return BootstrapPlan(
        scheme="ring",
        n_nodes=n,
        seeds=seeds,
        launch_order=launch_order,
    )


def build_plan(scheme: str, contacts: list[Contact]) -> BootstrapPlan:
    if scheme == "star":
        return build_star(contacts)
    if scheme == "ring":
        return build_ring(contacts)
    raise BootstrapError(f"Неизвестная bootstrap-схема: {scheme!r}")
def build_tree(contacts: list[Contact]) -> BootstrapPlan:
    if len(contacts) < 4:
        raise BootstrapError("tree требует не менее 4 узлов")
    seed = contacts[0]
    middle = contacts[1:4]
    leaves = contacts[4:]

    seeds: dict[str, list[Contact]] = {}
    launch_order: list[str] = [seed.node_id_hex]
    seeds[seed.node_id_hex] = []

    for m in middle:
        seeds[m.node_id_hex] = [seed]
        launch_order.append(m.node_id_hex)

    for i, leaf in enumerate(leaves):
        parent = middle[i % len(middle)]
        seeds[leaf.node_id_hex] = [parent]
        launch_order.append(leaf.node_id_hex)

    return BootstrapPlan(
        scheme="tree",
        n_nodes=len(contacts),
        seeds=seeds,
        launch_order=launch_order,
    )


def build_multi_seed(
    contacts: list[Contact],
    seed_count: int = 3,
) -> BootstrapPlan:
    if seed_count < 1:
        raise BootstrapError("seed_count должен быть >= 1")
    if len(contacts) < seed_count + 1:
        raise BootstrapError(
            f"multi_seed требует не менее {seed_count + 1} узлов"
        )
    seeds_list = contacts[:seed_count]
    leaves = contacts[seed_count:]

    seeds: dict[str, list[Contact]] = {}
    launch_order: list[str] = []
    for s in seeds_list:
        seeds[s.node_id_hex] = []
        launch_order.append(s.node_id_hex)
    for leaf in leaves:
        seeds[leaf.node_id_hex] = list(seeds_list)
        launch_order.append(leaf.node_id_hex)

    return BootstrapPlan(
        scheme="multi-seed",
        n_nodes=len(contacts),
        seeds=seeds,
        launch_order=launch_order,
    )


def build_plan(scheme: str, contacts: list[Contact]) -> BootstrapPlan:
    if scheme == "star":
        return build_star(contacts)
    if scheme == "ring":
        return build_ring(contacts)
    if scheme == "tree":
        return build_tree(contacts)
    if scheme in ("multi-seed", "multi_seed"):
        return build_multi_seed(contacts)
    raise BootstrapError(f"Неизвестная bootstrap-схема: {scheme!r}")