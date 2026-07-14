"""Suivi de contact SOBRE (closers Ambient Home), TDD. Aucun réseau.

Couvre : création d'activité + touche `updated_at`, validation du type, tri
desc + pagination du journal, journal AUTO du changement de statut ('statut',
ancien -> nouveau), prochaine action (pose + effacement ensemble), buckets
« À relancer » (en_retard / aujourdhui / cette_semaine) + exclusion gagne/perdu,
compteur du badge, migrations (colonne next_action + table contact_activities),
et la qualification cross-canal (issue/raison/detail — cf.
docs/plans/2026-07-14-qualification-contacts-design.md) : validation N1/N2/N3,
optionnalité, non-régression (aucune écriture sur la fiche). Les agrégats de
monitoring et le batch last-issues sont dans tests/test_qualification.py.
"""
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import QUALIF_DETAILS, QUALIF_ISSUES, QUALIF_RAISONS, ContactActivity, Opportunity
from app.routes.activities import (
    add_activity,
    list_activities,
    set_next_action,
    update_activity_detail,
)
from app.routes.followups import get_follow_ups, get_follow_ups_count
from app.routes.opportunities import list_opportunities, update_status
from app.schemas import ContactActivityCreate, ContactActivityDetailUpdate, NextActionUpdate, StatusUpdate


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _opp(session, name="Studio", status="non_contacte", follow_up=None,
         score=5, pop="architecte"):
    o = Opportunity(
        establishment_name=name, establishment_type="architecte d'intérieur",
        city="Paris", address="", main_signal="prescripteur actif",
        detection_date=date(2026, 7, 1), estimated_timing="",
        opportunity_score=score, status=status, population=pop,
        next_follow_up_date=follow_up,
    )
    session.add(o)
    session.commit()
    session.refresh(o)
    return o


# --- POST /activities ---------------------------------------------------------


def test_add_activity_persists_and_touches_updated_at():
    with Session(_engine()) as s:
        opp = _opp(s)
        opp.updated_at = datetime(2020, 1, 1)
        s.add(opp)
        s.commit()

        act = add_activity(opp.id, ContactActivityCreate(type="appel", note="RAS"), s)
        assert act.id is not None
        assert act.type == "appel" and act.note == "RAS"
        assert act.opportunity_id == opp.id

        s.refresh(opp)
        assert opp.updated_at > datetime(2020, 1, 1)  # l'activité fait vivre la fiche
        # Le statut n'est JAMAIS touché par un geste rapide.
        assert opp.status == "non_contacte"


def test_add_activity_rejects_unknown_type():
    with Session(_engine()) as s:
        opp = _opp(s)
        with pytest.raises(HTTPException) as exc:
            add_activity(opp.id, ContactActivityCreate(type="pigeon"), s)
        assert exc.value.status_code == 422


def test_add_activity_404_on_missing_opp():
    with Session(_engine()) as s:
        with pytest.raises(HTTPException) as exc:
            add_activity(999, ContactActivityCreate(type="note"), s)
        assert exc.value.status_code == 404


def test_add_activity_note_type_with_inline_text():
    with Session(_engine()) as s:
        opp = _opp(s)
        act = add_activity(opp.id, ContactActivityCreate(type="note", note="relancé par mail"), s)
        assert act.type == "note" and act.note == "relancé par mail"


def test_add_activity_accepts_optional_author():
    """`author` (fondation des comptes closers) : accepté en écriture et exposé
    en lecture. NULL par défaut tant que l'auth ne le renseigne pas."""
    with Session(_engine()) as s:
        opp = _opp(s)
        # Écriture optionnelle : fournie -> persistée et relue.
        act = add_activity(
            opp.id, ContactActivityCreate(type="appel", author="marie"), s
        )
        assert act.author == "marie"
        # Omise -> NULL (défaut), l'app fonctionne sans auth.
        act2 = add_activity(opp.id, ContactActivityCreate(type="note", note="RAS"), s)
        assert act2.author is None


# --- Qualification cross-canal : issue (N1) / raison (N2) / detail (N3) -------


def test_add_activity_accepts_valid_issue_raison_detail():
    with Session(_engine()) as s:
        opp = _opp(s)
        act = add_activity(
            opp.id,
            ContactActivityCreate(
                type="appel", issue="joint",
                raison="pas_interesse", detail=["deja_fournisseur", "budget"],
            ),
            s,
        )
        assert act.issue == "joint"
        assert act.raison == "pas_interesse"
        assert act.detail == ["deja_fournisseur", "budget"]


def test_add_activity_accepts_all_detail_chips():
    """Chaque chip N3 déclarée dans QUALIF_DETAILS est acceptée individuellement
    (N3 toujours optionnel, jamais bloquant)."""
    with Session(_engine()) as s:
        opp = _opp(s)
        for chip in QUALIF_DETAILS:
            act = add_activity(
                opp.id,
                ContactActivityCreate(type="appel", issue="joint", detail=[chip]),
                s,
            )
            assert act.detail == [chip]


def test_add_activity_issue_and_raison_are_optional():
    """`issue`/`raison`/`detail` sont TOUJOURS optionnels — ex. une émission
    (« Email envoyé ») n'a pas encore de résultat connu."""
    with Session(_engine()) as s:
        opp = _opp(s)
        act = add_activity(opp.id, ContactActivityCreate(type="email"), s)
        assert act.issue is None
        assert act.raison is None
        assert act.detail == []


def test_add_activity_rejects_unknown_issue():
    with Session(_engine()) as s:
        opp = _opp(s)
        with pytest.raises(HTTPException) as exc:
            add_activity(opp.id, ContactActivityCreate(type="appel", issue="furieux"), s)
        assert exc.value.status_code == 422


def test_add_activity_rejects_raison_not_matching_type_and_issue():
    with Session(_engine()) as s:
        opp = _opp(s)
        # 'mauvais_numero' est une raison KO d'appel, pas une raison JOINT.
        with pytest.raises(HTTPException) as exc:
            add_activity(
                opp.id,
                ContactActivityCreate(type="appel", issue="joint", raison="mauvais_numero"),
                s,
            )
        assert exc.value.status_code == 422


def test_add_activity_rejects_raison_without_issue():
    """Une raison sans issue n'a aucun (type, issue) pour la valider -> 422."""
    with Session(_engine()) as s:
        opp = _opp(s)
        with pytest.raises(HTTPException) as exc:
            add_activity(opp.id, ContactActivityCreate(type="appel", raison="interesse"), s)
        assert exc.value.status_code == 422


def test_add_activity_rejects_raison_from_wrong_channel():
    """'bounce' est une raison KO email, pas une raison KO appel."""
    with Session(_engine()) as s:
        opp = _opp(s)
        with pytest.raises(HTTPException) as exc:
            add_activity(
                opp.id,
                ContactActivityCreate(type="appel", issue="ko", raison="bounce"),
                s,
            )
        assert exc.value.status_code == 422


def test_add_activity_rejects_unknown_detail_chip():
    with Session(_engine()) as s:
        opp = _opp(s)
        with pytest.raises(HTTPException) as exc:
            add_activity(
                opp.id,
                ContactActivityCreate(type="appel", issue="joint", detail=["extraterrestre"]),
                s,
            )
        assert exc.value.status_code == 422


def test_add_activity_all_taxonomy_combinations_are_accepted():
    """Chaque (type, issue) déclaré dans QUALIF_RAISONS accepte bien TOUTES ses
    raisons -- garde-fou anti-régression si la taxonomie évolue."""
    with Session(_engine()) as s:
        opp = _opp(s)
        for (activity_type, issue), raisons in QUALIF_RAISONS.items():
            for raison in raisons:
                act = add_activity(
                    opp.id,
                    ContactActivityCreate(type=activity_type, issue=issue, raison=raison),
                    s,
                )
                assert act.issue == issue and act.raison == raison


def test_add_activity_qualification_never_writes_status_or_other_fields():
    """Non-régression (décision d'Alexis) : un geste de qualification, quel que
    soit `issue`, n'écrit JAMAIS `status` ni aucun autre champ métier de la
    fiche -- seule `updated_at` bouge (comme n'importe quel autre geste)."""
    with Session(_engine()) as s:
        opp = _opp(s, status="non_contacte")
        snapshot = {
            "status": opp.status,
            "next_follow_up_date": opp.next_follow_up_date,
            "next_action": opp.next_action,
            "assigned_to": opp.assigned_to,
        }
        for issue, raison in [
            ("ko", "mauvais_numero"), ("ko", "ne_plus_contacter"),
            ("pas_joint", "pas_de_reponse"), ("joint", "pas_interesse"),
        ]:
            add_activity(
                opp.id, ContactActivityCreate(type="appel", issue=issue, raison=raison), s
            )
            s.refresh(opp)
            assert opp.status == snapshot["status"]
            assert opp.next_follow_up_date == snapshot["next_follow_up_date"]
            assert opp.next_action == snapshot["next_action"]
            assert opp.assigned_to == snapshot["assigned_to"]


def test_add_activity_qualification_exposed_in_read():
    """`ContactActivityRead` expose issue/raison/detail (lecture)."""
    with Session(_engine()) as s:
        opp = _opp(s)
        base = datetime(2026, 7, 10, 9, 0, 0)
        s.add(ContactActivity(
            opportunity_id=opp.id, type="appel", issue="ko", raison="mauvais_numero",
            detail=[], created_at=base,
        ))
        s.commit()
        rows = list_activities(opp.id, s)
        assert rows[0].issue == "ko"
        assert rows[0].raison == "mauvais_numero"
        assert rows[0].detail == []


def test_contact_activity_read_coerces_null_detail_to_empty_list():
    """`ContactActivityRead` (le schéma exposé par l'API) coerce `detail=NULL`
    (ligne héritée pré-migration, ou insérée hors ORM) en liste vide -- même
    pattern que `OpportunityList._coerce_none_list`."""
    from app.schemas import ContactActivityRead

    with Session(_engine()) as s:
        opp = _opp(s)
        act = ContactActivity(opportunity_id=opp.id, type="note")
        s.add(act)
        s.commit()
        s.refresh(act)
        # Simule une ligne héritée : `detail` NULL directement en base.
        from sqlalchemy import text
        s.exec(text("UPDATE contact_activities SET detail = NULL WHERE id = :id").bindparams(id=act.id))
        s.commit()
        row = s.exec(select(ContactActivity).where(ContactActivity.id == act.id)).one()
        assert ContactActivityRead.model_validate(row).detail == []


def test_qualif_taxonomy_families_cover_documented_channels():
    """Garde-fou : les 3 canaux qualifiables (appel/email/dm_insta) ont bien
    leurs 3 familles N1 déclarées dans QUALIF_RAISONS."""
    for channel in ("appel", "email", "dm_insta"):
        for issue in QUALIF_ISSUES:
            assert (channel, issue) in QUALIF_RAISONS
            assert len(QUALIF_RAISONS[(channel, issue)]) > 0


def test_rdv_pris_is_joint_and_first_for_every_channel():
    """« RDV pris » (issue reine de la télévente) est une raison JOINT -- pas
    une case à part -- et vient en tête de la liste sur les 3 canaux (mise en
    avant visuelle côté front, ContactPanel.QUALIF_RAISON_HERO)."""
    for channel in ("appel", "email", "dm_insta"):
        raisons = QUALIF_RAISONS[(channel, "joint")]
        assert raisons[0] == "rdv_pris"
        with Session(_engine()) as s:
            opp = _opp(s)
            act = add_activity(
                opp.id,
                ContactActivityCreate(type=channel, issue="joint", raison="rdv_pris"),
                s,
            )
            assert act.issue == "joint" and act.raison == "rdv_pris"


def test_dm_insta_ko_uses_merged_compte_inaccessible_slug():
    """La taxonomie allégée fusionne les anciens 'compte_introuvable'/'bloque'
    (v1) en une seule raison 'compte_inaccessible' -- les deux anciens slugs
    ne sont plus acceptés en écriture (ils restent lisibles sur les vieilles
    activités, mais ne sont plus proposés)."""
    with Session(_engine()) as s:
        opp = _opp(s)
        act = add_activity(
            opp.id,
            ContactActivityCreate(type="dm_insta", issue="ko", raison="compte_inaccessible"),
            s,
        )
        assert act.raison == "compte_inaccessible"

        for legacy_raison in ("compte_introuvable", "bloque"):
            with pytest.raises(HTTPException) as exc:
                add_activity(
                    opp.id,
                    ContactActivityCreate(type="dm_insta", issue="ko", raison=legacy_raison),
                    s,
                )
            assert exc.value.status_code == 422


def test_email_no_longer_accepts_v1_a_suivre_or_desinscription():
    """Taxonomie allégée : 'a_suivre' (email/joint) et 'desinscription'
    (email/ko) ne sont plus des raisons sélectionnables (v1)."""
    with Session(_engine()) as s:
        opp = _opp(s)
        with pytest.raises(HTTPException) as exc:
            add_activity(
                opp.id, ContactActivityCreate(type="email", issue="joint", raison="a_suivre"), s
            )
        assert exc.value.status_code == 422
        with pytest.raises(HTTPException) as exc:
            add_activity(
                opp.id,
                ContactActivityCreate(type="email", issue="ko", raison="desinscription"),
                s,
            )
        assert exc.value.status_code == 422


# --- PATCH .../activities/{id}/detail : enrichit sans doublon ------------------


def test_update_activity_detail_sets_detail_and_note_without_new_row():
    with Session(_engine()) as s:
        opp = _opp(s)
        act = add_activity(
            opp.id, ContactActivityCreate(type="appel", issue="joint", raison="pas_interesse"), s
        )
        assert act.detail == [] and act.note is None

        updated = update_activity_detail(
            opp.id, act.id,
            ContactActivityDetailUpdate(detail=["deja_fournisseur"], note="rappellera en sept."),
            s,
        )
        assert updated.id == act.id  # même ligne, pas un doublon
        assert updated.detail == ["deja_fournisseur"]
        assert updated.note == "rappellera en sept."
        # issue/raison/type restent ceux du POST initial, jamais réécrits ici.
        assert updated.issue == "joint" and updated.raison == "pas_interesse"
        assert updated.type == "appel"

        rows = s.exec(select(ContactActivity).where(
            ContactActivity.opportunity_id == opp.id)).all()
        assert len(rows) == 1  # toujours une seule activité


def test_update_activity_detail_partial_update_leaves_omitted_field_unchanged():
    with Session(_engine()) as s:
        opp = _opp(s)
        act = add_activity(
            opp.id, ContactActivityCreate(type="appel", issue="ko", raison="mauvais_numero"), s
        )
        update_activity_detail(
            opp.id, act.id, ContactActivityDetailUpdate(detail=["budget"]), s
        )
        # `note` omis -> inchangé (None), pas écrasé.
        s.refresh(act)
        assert act.detail == ["budget"]
        assert act.note is None


def test_update_activity_detail_rejects_unknown_chip():
    with Session(_engine()) as s:
        opp = _opp(s)
        act = add_activity(opp.id, ContactActivityCreate(type="appel", issue="joint"), s)
        with pytest.raises(HTTPException) as exc:
            update_activity_detail(
                opp.id, act.id, ContactActivityDetailUpdate(detail=["extraterrestre"]), s
            )
        assert exc.value.status_code == 422


def test_update_activity_detail_404_on_missing_activity():
    with Session(_engine()) as s:
        opp = _opp(s)
        with pytest.raises(HTTPException) as exc:
            update_activity_detail(opp.id, 999, ContactActivityDetailUpdate(note="x"), s)
        assert exc.value.status_code == 404


def test_update_activity_detail_404_when_activity_belongs_to_other_opportunity():
    with Session(_engine()) as s:
        opp1 = _opp(s, name="A")
        opp2 = _opp(s, name="B")
        act = add_activity(opp1.id, ContactActivityCreate(type="appel", issue="joint"), s)
        with pytest.raises(HTTPException) as exc:
            update_activity_detail(opp2.id, act.id, ContactActivityDetailUpdate(note="x"), s)
        assert exc.value.status_code == 404


def test_update_activity_detail_never_writes_status():
    with Session(_engine()) as s:
        opp = _opp(s, status="non_contacte")
        act = add_activity(opp.id, ContactActivityCreate(type="appel", issue="ko"), s)
        update_activity_detail(opp.id, act.id, ContactActivityDetailUpdate(detail=["budget"]), s)
        s.refresh(opp)
        assert opp.status == "non_contacte"


# --- GET /opportunities?has_activity= : filtre « jamais travaillé » -----------


def test_has_activity_false_excludes_opportunities_with_any_activity():
    """Corrige l'approximation status=='non_contacte' de « Jamais appelés » :
    une fiche qualifiée (issue quelconque) ne doit plus ressortir, même si son
    statut n'a pas bougé (invariant : la qualification ne réécrit jamais le
    statut)."""
    with Session(_engine()) as s:
        called = _opp(s, name="Called")
        never = _opp(s, name="Never")
        add_activity(called.id, ContactActivityCreate(type="appel", issue="pas_joint", raison="repondeur"), s)

        got = list_opportunities(session=s, population="architecte", has_activity=False)
        assert [o.establishment_name for o in got] == ["Never"]


def test_has_activity_false_excludes_emission_with_null_issue():
    """Une émission (« Email envoyé », issue=NULL) compte déjà comme « travaillé »
    -- ce n'est PAS une tentative qualifiée mais la fiche n'est plus « jamais
    contactée »."""
    with Session(_engine()) as s:
        emitted = _opp(s, name="Emitted")
        add_activity(emitted.id, ContactActivityCreate(type="email"), s)

        got = list_opportunities(session=s, population="architecte", has_activity=False)
        assert got == []


def test_has_activity_true_returns_only_opportunities_with_activity():
    with Session(_engine()) as s:
        called = _opp(s, name="Called")
        never = _opp(s, name="Never")
        add_activity(called.id, ContactActivityCreate(type="appel", issue="joint"), s)

        got = list_opportunities(session=s, population="architecte", has_activity=True)
        assert [o.establishment_name for o in got] == ["Called"]


def test_has_activity_omitted_does_not_filter():
    with Session(_engine()) as s:
        called = _opp(s, name="Called")
        never = _opp(s, name="Never")
        add_activity(called.id, ContactActivityCreate(type="appel", issue="joint"), s)

        got = list_opportunities(session=s, population="architecte")
        assert sorted(o.establishment_name for o in got) == ["Called", "Never"]


# --- GET /activities : tri desc + pagination ----------------------------------


def test_list_activities_desc_and_pagination():
    with Session(_engine()) as s:
        opp = _opp(s)
        base = datetime(2026, 7, 10, 9, 0, 0)
        for i in range(3):
            s.add(ContactActivity(
                opportunity_id=opp.id, type="appel", note=f"a{i}",
                created_at=base + timedelta(hours=i),
            ))
        s.commit()

        rows = list_activities(opp.id, s)
        # plus récent d'abord
        assert [r.note for r in rows] == ["a2", "a1", "a0"]

        # pagination légère
        page = list_activities(opp.id, s, limit=2, offset=0)
        assert [r.note for r in page] == ["a2", "a1"]
        page2 = list_activities(opp.id, s, limit=2, offset=2)
        assert [r.note for r in page2] == ["a0"]


def test_list_activities_404_on_missing_opp():
    with Session(_engine()) as s:
        with pytest.raises(HTTPException) as exc:
            list_activities(999, s)
        assert exc.value.status_code == 404


# --- PATCH status : journal AUTO d'une activité 'statut' ----------------------


def test_status_change_auto_journals_statut_activity():
    with Session(_engine()) as s:
        opp = _opp(s, status="non_contacte")
        update_status(opp.id, StatusUpdate(status="contacte"), s)

        acts = s.exec(select(ContactActivity).where(
            ContactActivity.opportunity_id == opp.id)).all()
        assert len(acts) == 1
        assert acts[0].type == "statut"
        assert acts[0].note == "non_contacte -> contacte"


def test_status_unchanged_does_not_journal():
    # Pas le fouilli : re-poser le MÊME statut (ex. juste (re)planifier une
    # relance) ne crée pas d'activité 'statut'.
    with Session(_engine()) as s:
        opp = _opp(s, status="contacte")
        update_status(
            opp.id,
            StatusUpdate(status="contacte", next_follow_up_date=date(2026, 8, 1)),
            s,
        )
        acts = s.exec(select(ContactActivity).where(
            ContactActivity.opportunity_id == opp.id)).all()
        assert acts == []


# --- PUT /next-action : pose + effacement ensemble ----------------------------


def test_set_next_action_sets_both():
    with Session(_engine()) as s:
        opp = _opp(s)
        out = set_next_action(
            opp.id,
            NextActionUpdate(next_action="Rappeler le gérant", next_follow_up_date=date(2026, 7, 20)),
            s,
        )
        assert out.next_action == "Rappeler le gérant"
        assert out.next_follow_up_date == date(2026, 7, 20)


def test_set_next_action_clears_both_when_empty():
    with Session(_engine()) as s:
        opp = _opp(s, follow_up=date(2026, 7, 20))
        opp.next_action = "vieux texte"
        s.add(opp)
        s.commit()

        out = set_next_action(opp.id, NextActionUpdate(), s)  # {} => efface les deux
        assert out.next_action is None
        assert out.next_follow_up_date is None


# --- GET /followups : buckets + exclusion + compteur --------------------------


def test_followups_buckets():
    today = date.today()
    with Session(_engine()) as s:
        _opp(s, name="Retard", follow_up=today - timedelta(days=2))
        _opp(s, name="Aujourdhui", follow_up=today)
        _opp(s, name="Semaine", follow_up=today + timedelta(days=3))
        _opp(s, name="PlusTard", follow_up=today + timedelta(days=30))  # exclu
        _opp(s, name="SansDate", follow_up=None)  # exclu

        buckets = get_follow_ups(session=s, population="architecte")
        assert [o.establishment_name for o in buckets.en_retard] == ["Retard"]
        assert [o.establishment_name for o in buckets.aujourdhui] == ["Aujourdhui"]
        assert [o.establishment_name for o in buckets.cette_semaine] == ["Semaine"]
        # next_action est exposé sur chaque fiche listée.
        assert hasattr(buckets.en_retard[0], "next_action")


def test_followups_excludes_gagne_perdu():
    today = date.today()
    with Session(_engine()) as s:
        _opp(s, name="Gagne", status="gagne", follow_up=today - timedelta(days=1))
        _opp(s, name="Perdu", status="perdu", follow_up=today)
        _opp(s, name="Actif", status="relance", follow_up=today)

        buckets = get_follow_ups(session=s, population="architecte")
        names = ([o.establishment_name for o in buckets.en_retard]
                 + [o.establishment_name for o in buckets.aujourdhui]
                 + [o.establishment_name for o in buckets.cette_semaine])
        assert names == ["Actif"]


def test_followups_ordered_by_due_date():
    today = date.today()
    with Session(_engine()) as s:
        _opp(s, name="J-1", follow_up=today - timedelta(days=1))
        _opp(s, name="J-5", follow_up=today - timedelta(days=5))
        buckets = get_follow_ups(session=s, population="architecte")
        # le plus en retard d'abord (échéance croissante)
        assert [o.establishment_name for o in buckets.en_retard] == ["J-5", "J-1"]


def test_followups_population_filter():
    today = date.today()
    with Session(_engine()) as s:
        _opp(s, name="Arc", pop="architecte", follow_up=today)
        _opp(s, name="Chr", pop="chr", follow_up=today)
        arc = get_follow_ups(session=s, population="architecte")
        assert [o.establishment_name for o in arc.aujourdhui] == ["Arc"]
        allpop = get_follow_ups(session=s, population="")  # vide = toutes
        assert sorted(o.establishment_name for o in allpop.aujourdhui) == ["Arc", "Chr"]


def test_followups_count():
    today = date.today()
    with Session(_engine()) as s:
        _opp(s, name="R1", follow_up=today - timedelta(days=2))
        _opp(s, name="R2", follow_up=today - timedelta(days=1))
        _opp(s, name="A1", follow_up=today)
        _opp(s, name="S1", follow_up=today + timedelta(days=4))
        _opp(s, name="Loin", follow_up=today + timedelta(days=40))  # exclu

        c = get_follow_ups_count(session=s, population="architecte")
        assert c.en_retard == 2
        assert c.aujourdhui == 1
        assert c.cette_semaine == 1
        assert c.total == 4


# --- Migrations ---------------------------------------------------------------


def test_migration_adds_next_action_column(tmp_path):
    from sqlalchemy import create_engine as ce, inspect, text
    import app.database as db

    url = f"sqlite:///{tmp_path/'legacy.db'}"
    old = ce(url)
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE opportunities (id INTEGER PRIMARY KEY, "
                          "establishment_name VARCHAR, establishment_type VARCHAR, "
                          "city VARCHAR, address VARCHAR, main_signal VARCHAR, "
                          "detection_date DATE, estimated_timing VARCHAR)"))
    old.dispose()

    orig_engine, orig_url = db.engine, db.DATABASE_URL
    db.engine, db.DATABASE_URL = ce(url), url
    try:
        db._run_lightweight_migrations()
        cols = {c["name"] for c in inspect(db.engine).get_columns("opportunities")}
        assert "next_action" in cols
    finally:
        db.engine.dispose()
        db.engine, db.DATABASE_URL = orig_engine, orig_url


def test_migration_adds_author_column_on_existing_table(tmp_path):
    """Base existante avec un `contact_activities` SANS `author` (créé avant
    l'évolution) : la migration légère ajoute la colonne sans casser la table."""
    from sqlalchemy import create_engine as ce, inspect, text
    import app.database as db

    url = f"sqlite:///{tmp_path/'legacy_ca.db'}"
    old = ce(url)
    with old.begin() as conn:
        # opportunities minimal (garde-fou d'entrée de la migration).
        conn.execute(text("CREATE TABLE opportunities (id INTEGER PRIMARY KEY, "
                          "establishment_name VARCHAR, establishment_type VARCHAR, "
                          "city VARCHAR, address VARCHAR, main_signal VARCHAR, "
                          "detection_date DATE, estimated_timing VARCHAR)"))
        # contact_activities ancien : pas de colonne author.
        conn.execute(text("CREATE TABLE contact_activities (id INTEGER PRIMARY KEY, "
                          "opportunity_id INTEGER, type VARCHAR, note VARCHAR, "
                          "created_at DATETIME)"))
    old.dispose()

    orig_engine, orig_url = db.engine, db.DATABASE_URL
    db.engine, db.DATABASE_URL = ce(url), url
    try:
        db._run_lightweight_migrations()
        cols = {c["name"] for c in inspect(db.engine).get_columns("contact_activities")}
        assert "author" in cols
    finally:
        db.engine.dispose()
        db.engine, db.DATABASE_URL = orig_engine, orig_url


def test_migration_adds_qualification_columns_on_existing_table(tmp_path):
    """Base existante avec un `contact_activities` SANS issue/raison/detail
    (créé avant la qualification cross-canal) : la migration légère ajoute les
    3 colonnes sans casser la table, et les anciennes lignes restent valides."""
    from sqlalchemy import create_engine as ce, inspect, text
    import app.database as db

    url = f"sqlite:///{tmp_path/'legacy_qualif.db'}"
    old = ce(url)
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE opportunities (id INTEGER PRIMARY KEY, "
                          "establishment_name VARCHAR, establishment_type VARCHAR, "
                          "city VARCHAR, address VARCHAR, main_signal VARCHAR, "
                          "detection_date DATE, estimated_timing VARCHAR)"))
        # contact_activities ancien : author présent (migration précédente) mais
        # pas issue/raison/detail (nouvelle migration).
        conn.execute(text("CREATE TABLE contact_activities (id INTEGER PRIMARY KEY, "
                          "opportunity_id INTEGER, type VARCHAR, note VARCHAR, "
                          "author VARCHAR, created_at DATETIME)"))
        conn.execute(text(
            "INSERT INTO contact_activities (opportunity_id, type, note, created_at) "
            "VALUES (1, 'appel', 'Répondu', '2026-01-01 10:00:00')"
        ))
    old.dispose()

    orig_engine, orig_url = db.engine, db.DATABASE_URL
    db.engine, db.DATABASE_URL = ce(url), url
    try:
        db._run_lightweight_migrations()
        cols = {c["name"] for c in inspect(db.engine).get_columns("contact_activities")}
        assert {"issue", "raison", "detail"} <= cols
        # La ligne pré-existante reste lisible, `issue` est NULL (pas de backfill).
        with Session(db.engine) as s:
            row = s.exec(select(ContactActivity)).first()
            assert row.note == "Répondu"
            assert row.issue is None
    finally:
        db.engine.dispose()
        db.engine, db.DATABASE_URL = orig_engine, orig_url


def test_contact_activities_table_created_by_create_all():
    # La NOUVELLE table est créée par create_all (conditionnel, checkfirst) —
    # idempotent : deux appels ne cassent rien, et on peut y insérer/relire.
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    SQLModel.metadata.create_all(e)  # idempotence
    with Session(e) as s:
        opp = _opp(s)
        s.add(ContactActivity(opportunity_id=opp.id, type="dm_insta", note="DM"))
        s.commit()
        got = s.exec(select(ContactActivity)).all()
        assert len(got) == 1 and got[0].type == "dm_insta"
