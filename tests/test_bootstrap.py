import secrets

import pytest

from overlay.bootstrap import (
    BootstrapError,
    build_plan,
    build_ring,
    build_star,
)
from overlay.dht.contact import Contact


def _contact(i: int) -> Contact:
    return Contact(
        node_id=bytes([i]) * 32,
        host="127.0.0.1",
        port=9000 + i,
    )


def test_star_requires_two_nodes():
    with pytest.raises(BootstrapError):
        build_star([_contact(0)])


def test_ring_requires_three_nodes():
    with pytest.raises(BootstrapError):
        build_ring([_contact(0), _contact(1)])


def test_build_plan_unknown_scheme():
    with pytest.raises(BootstrapError):
        build_plan("mesh", [_contact(0), _contact(1)])


def test_star_seeds_all_go_to_first():
    contacts = [_contact(i) for i in range(5)]
    plan = build_star(contacts)

    assert plan.scheme == "star"
    assert plan.n_nodes == 5
    assert plan.launch_order[0] == contacts[0].node_id_hex
    assert plan.seeds_for(contacts[0].node_id_hex) == []
    for c in contacts[1:]:
        assert plan.seeds_for(c.node_id_hex) == [contacts[0]]


def test_ring_each_node_seeds_successor():
    contacts = [_contact(i) for i in range(4)]
    plan = build_ring(contacts)

    assert plan.scheme == "ring"
    assert plan.n_nodes == 4
    for i, c in enumerate(contacts):
        successor = contacts[(i + 1) % len(contacts)]
        assert plan.seeds_for(c.node_id_hex) == [successor]


def test_plan_describe_is_serializable():
    contacts = [_contact(i) for i in range(3)]
    plan = build_plan("star", contacts)
    desc = plan.describe()

    assert desc["scheme"] == "star"
    assert desc["n_nodes"] == 3
    assert isinstance(desc["launch_order"], list)
    assert all(isinstance(x, str) for x in desc["launch_order"])
    for node_id, seed_ids in desc["seeds"].items():
        assert isinstance(node_id, str)
        assert isinstance(seed_ids, list)


def test_ring_distributes_seeds_not_concentrated():
    contacts = [_contact(i) for i in range(6)]
    plan = build_ring(contacts)

    seed_counts = {}
    for seed_list in plan.seeds.values():
        for seed in seed_list:
            seed_counts[seed.node_id_hex] = seed_counts.get(seed.node_id_hex, 0) + 1

    for c in contacts:
        assert seed_counts.get(c.node_id_hex, 0) == 1


def test_ring_seeds_differ_from_star():
    contacts = [_contact(i) for i in range(5)]
    star_plan = build_star(contacts)
    ring_plan = build_ring(contacts)

    star_unique = {
        s.node_id_hex
        for seeds in star_plan.seeds.values()
        for s in seeds
    }
    ring_unique = {
        s.node_id_hex
        for seeds in ring_plan.seeds.values()
        for s in seeds
    }

    assert len(star_unique) == 1
    assert len(ring_unique) == 5


def test_random_contacts_do_not_collide():
    contacts = [
        Contact(node_id=secrets.token_bytes(32), host="127.0.0.1", port=9000 + i)
        for i in range(5)
    ]
    plan = build_ring(contacts)
    all_ids = {c.node_id_hex for c in contacts}
    for seeds in plan.seeds.values():
        for s in seeds:
            assert s.node_id_hex in all_ids