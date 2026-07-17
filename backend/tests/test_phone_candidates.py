"""Helper pur `services/phone_candidates.py` (chantier multi-numéros), TDD.
Aucun réseau, aucune session DB : `opp` est un objet léger (SimpleNamespace)
avec `phone`/`phone_candidates`, comme le veut le module. Cf.
docs/plans/2026-07-17-multi-numeros-design.md."""
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.phone_candidates import (
    MAX_PHONE_CANDIDATES,
    PromoteError,
    add_candidate,
    promote,
)


def _opp(phone=None, candidates=None):
    return SimpleNamespace(phone=phone, phone_candidates=list(candidates or []))


# --- add_candidate --------------------------------------------------------------


def test_add_candidate_normalizes_and_appends():
    opp = _opp(phone="01 23 45 67 89")
    ok = add_candidate(opp, "0298884285", "annuaire", today=date(2026, 7, 17))
    assert ok is True
    assert opp.phone_candidates == [{
        "number": "02 98 88 42 85", "source": "annuaire", "first_seen": "2026-07-17",
    }]


def test_add_candidate_carries_proof_url_when_given():
    opp = _opp(phone="01 23 45 67 89")
    add_candidate(opp, "02 98 88 42 85", "site", proof_url="https://x.fr", today=date(2026, 7, 17))
    assert opp.phone_candidates[0]["proof_url"] == "https://x.fr"


def test_add_candidate_omits_proof_url_when_absent():
    opp = _opp(phone="01 23 45 67 89")
    add_candidate(opp, "02 98 88 42 85", "places", today=date(2026, 7, 17))
    assert "proof_url" not in opp.phone_candidates[0]


def test_add_candidate_rejects_implausible_number():
    opp = _opp(phone="01 23 45 67 89")
    assert add_candidate(opp, "12345", "site") is False
    assert opp.phone_candidates == []


def test_add_candidate_rejects_none_number():
    opp = _opp(phone="01 23 45 67 89")
    assert add_candidate(opp, None, "site") is False


def test_add_candidate_rejects_duplicate_of_principal():
    # Même numéro que le principal (formats différents) -> jamais un candidat.
    opp = _opp(phone="02 49 88 42 85")
    assert add_candidate(opp, "0249884285", "annuaire") is False
    assert opp.phone_candidates == []


def test_add_candidate_rejects_duplicate_between_candidates():
    opp = _opp(phone="01 23 45 67 89")
    add_candidate(opp, "02 49 88 42 85", "site", today=date(2026, 7, 1))
    ok = add_candidate(opp, "02.49.88.42.85", "annuaire", today=date(2026, 7, 17))
    assert ok is False
    assert len(opp.phone_candidates) == 1
    assert opp.phone_candidates[0]["source"] == "site"  # le premier vu gagne


def test_add_candidate_is_idempotent():
    opp = _opp(phone="01 23 45 67 89")
    add_candidate(opp, "02 49 88 42 85", "site")
    add_candidate(opp, "02 49 88 42 85", "site")
    assert len(opp.phone_candidates) == 1


def test_add_candidate_hard_cap_at_five():
    opp = _opp(phone="01 23 45 67 89")
    numbers = ["06 00 00 00 0" + str(i) for i in range(6)]
    added = [add_candidate(opp, n, "site") for n in numbers]
    assert added == [True, True, True, True, True, False]
    assert len(opp.phone_candidates) == MAX_PHONE_CANDIDATES
    # L'ordre first_seen déjà en place n'est pas perturbé par le rejet.
    assert opp.phone_candidates[0]["number"] == "06 00 00 00 00"


def test_add_candidate_accepts_monaco_number():
    opp = _opp(phone="01 23 45 67 89")
    ok = add_candidate(opp, "+377 92 05 23 21", "site")
    assert ok is True
    assert opp.phone_candidates[0]["number"] == "+377 92 05 23 21"


def test_add_candidate_works_when_principal_empty():
    opp = _opp(phone=None)
    assert add_candidate(opp, "06 12 34 56 78", "places") is True


# --- promote ----------------------------------------------------------------------


def test_promote_swaps_principal_and_demotes_old_one():
    opp = _opp(
        phone="02 49 88 42 85",
        candidates=[{"number": "02 85 52 84 93", "source": "site", "first_seen": "2026-07-10"}],
    )
    promoted = promote(opp, "02 85 52 84 93", today=date(2026, 7, 17))
    assert promoted["number"] == "02 85 52 84 93"
    assert promoted["source"] == "site"
    assert opp.phone == "02 85 52 84 93"
    assert opp.phone_candidates == [{
        "number": "02 49 88 42 85", "source": "ex_principal", "first_seen": "2026-07-17",
    }]


def test_promote_normalizes_input_number():
    opp = _opp(
        phone="01 23 45 67 89",
        candidates=[{"number": "02 85 52 84 93", "source": "site", "first_seen": "2026-07-10"}],
    )
    promote(opp, "0285528493")
    assert opp.phone == "02 85 52 84 93"


def test_promote_without_prior_principal_no_ex_principal_created():
    opp = _opp(
        phone=None,
        candidates=[{"number": "02 85 52 84 93", "source": "annuaire", "first_seen": "2026-07-10"}],
    )
    promote(opp, "02 85 52 84 93")
    assert opp.phone == "02 85 52 84 93"
    assert opp.phone_candidates == []


def test_promote_raises_when_number_not_a_candidate():
    opp = _opp(phone="01 23 45 67 89", candidates=[])
    with pytest.raises(PromoteError):
        promote(opp, "02 85 52 84 93")


def test_promote_reapplies_cap_and_dedup_for_ex_principal():
    # 5 candidats déjà présents (cap plein) : promouvoir l'un d'eux libère une
    # place, l'ex_principal la prend -> toujours <= 5, jamais de doublon.
    candidates = [
        {"number": f"06 00 00 00 0{i}", "source": "site", "first_seen": "2026-07-01"}
        for i in range(5)
    ]
    opp = _opp(phone="01 23 45 67 89", candidates=candidates)
    promote(opp, "06 00 00 00 02")
    assert len(opp.phone_candidates) == 5
    numbers = [c["number"] for c in opp.phone_candidates]
    assert numbers.count("01 23 45 67 89") == 1
    assert "06 00 00 00 02" not in numbers
