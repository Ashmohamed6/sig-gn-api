from __future__ import annotations

import uuid
from typing import Any

from django.db import connection, transaction

from .etl import DatasetDefinition


class CorePublishError(RuntimeError):
    pass


def _count_scalar(sql: str, params: list[Any]) -> int:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    return int(row[0] if row else 0)


def _exec_rowcount(sql: str, params: list[Any]) -> int:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return int(cursor.rowcount or 0)


def _split_codes_sql(field_sql: str) -> str:
    # Kobo multi-select exports are typically space-separated codes.
    return f"NULLIF(regexp_split_to_array(BTRIM({field_sql}), '\\\\s+'), '{{}}')"


def publish_dataset_to_core(
    *,
    dataset: DatasetDefinition,
    project_code: str,
    region_id: str | None,
    import_batch: uuid.UUID | None = None,
) -> dict[str, Any]:
    code = str(dataset.code or "").strip().lower()

    if code == "agr-menages":
        return _publish_agr_menages(project_code=project_code, region_id=region_id, import_batch=import_batch)

    if code == "agr-organisations":
        return _publish_agr_organisations(project_code=project_code, region_id=region_id)

    if code == "agr-comites":
        return _publish_agr_comites(project_code=project_code, region_id=region_id)

    if code == "cep-parcelles":
        return _publish_cep_parcelles_from_kobo(project_code=project_code, region_id=region_id)

    if code == "agr-pratiques-rendements":
        return _publish_pratiques_agro(project_code=project_code, region_id=region_id)

    if code == "agr-intrants-comptoirs" or code == "agr-marches":
        return _publish_intrants_comptoirs(project_code=project_code, region_id=region_id)

    if code == "agr-intrants-distribution":
        return _publish_intrants_distribution(project_code=project_code, region_id=region_id)

    if code == "agr-ouvrages":
        return _publish_ouvrages(project_code=project_code, region_id=region_id)

    if code == "agr-couloirs":
        return _publish_couloirs(project_code=project_code, region_id=region_id)

    if code == "agr-stations-pluie":
        return _publish_meteo_stations(project_code=project_code, region_id=region_id)

    if code == "agr-mesures-meteo":
        return _publish_meteo_mesures(project_code=project_code, region_id=region_id)

    if code == "agr-tetes-sources":
        return _publish_tetes_sources(project_code=project_code, region_id=region_id)

    if code == "agr-zones-degradees":
        return _publish_zones_degradees(project_code=project_code, region_id=region_id)

    if code == "fiere-suivi-sortants":
        return _publish_fiere_suivi_sortants(project_code=project_code, region_id=region_id)

    if code == "fiere-entreprises":
        return _publish_fiere_entreprises(project_code=project_code, region_id=region_id)

    if code == "fiere-formations":
        return _publish_fiere_formations(project_code=project_code, region_id=region_id)

    if code == "fiere-emploi-insertion":
        return _publish_fiere_emploi_insertion(project_code=project_code, region_id=region_id)

    if code == "fiere-emploi-domaines":
        return _publish_fiere_emploi_domaines(project_code=project_code, region_id=region_id)

    if code == "fiere-insertion-domaines":
        return _publish_fiere_insertion_domaines(project_code=project_code, region_id=region_id)

    if code == "fiere-participation":
        return _publish_fiere_participation(project_code=project_code, region_id=region_id)

    raise CorePublishError(f"ETL stage->core non implemente pour '{dataset.code}'.")


def _publish_agr_menages(*, project_code: str, region_id: str | None, import_batch: uuid.UUID | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    # Restrict by region (manager scope) using ref mapping to be robust even if stage.region is missing.
    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    # Optional batch restriction (only when data was inserted with our import pipeline).
    if import_batch:
        filters.append("s.import_batch = %s")
        params.append(str(import_batch))

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.agr_menage_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
        """,
        params,
    )

    sql = f"""
        INSERT INTO core.agr_menage (
            raw_uuid,
            project_code,
            id_menage,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            nom_chef_menage,
            type_menage,
            type_menage_autres,
            nb_personnes,
            nb_enfants_u5,
            themes_sensibilisation_codes,
            themes_sensibilisation_autres,
            nb_seances_total,
            source_information_codes,
            source_information_autres,
            producteur_informe_intrants,
            obs_sensib,
            menage_prat_agroeco,
            pratiques_agro_codes,
            pratiques_agro_autres,
            utilise_intrants_chimiques,
            applique_bonnes_pratiques_intrants,
            utilise_foyer_ameliore,
            type_foyer_principal,
            type_foyer_principal_autres,
            annees_utilisation_foyer,
            obs_foyer,
            applique_bonnes_prat_nutrition,
            pratiques_nutrition_codes,
            pratiques_nutrition_autres,
            frequence_pratiques_nutrition,
            obs_nutrition,
            obs_menage,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT DISTINCT ON (s.project_code, s.id_menage)
            s.raw_uuid,
            s.project_code,
            s.id_menage,
            s.commune AS id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.nom_chef_menage,
            s.type_menage,
            s.type_menage_autres,
            s.nb_personnes,
            s.nb_enfants_u5,
            {_split_codes_sql('s.themes_sensibilisation')},
            s.themes_sensibilisation_autres,
            s.nb_seances_total,
            {_split_codes_sql('s.source_information')},
            s.source_information_autres,
            s.producteur_informe_intrants,
            s.obs_sensib,
            s.menage_prat_agroeco,
            {_split_codes_sql('s.pratiques_agro_menage')},
            s.pratiques_agro_autres,
            s.utilise_intrants_chimiques,
            s.applique_bonnes_pratiques_intrants,
            s.utilise_foyer_ameliore,
            s.type_foyer_principal,
            s.type_foyer_principal_autres,
            s.annees_utilisation_foyer,
            s.obs_foyer,
            s.applique_bonnes_prat_nutrition,
            {_split_codes_sql('s.pratiques_nutritionnelles')},
            s.pratiques_nutrition_autres,
            s.frequence_pratiques_nutrition,
            s.obs_nutrition,
            s.obs_menage,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.agr_menage_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
        ORDER BY s.project_code, s.id_menage, s.imported_at DESC NULLS LAST, s.raw_uuid DESC
        ON CONFLICT (project_code, id_menage) DO UPDATE SET
            raw_uuid = EXCLUDED.raw_uuid,
            id_menage = EXCLUDED.id_menage,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            nom_chef_menage = EXCLUDED.nom_chef_menage,
            type_menage = EXCLUDED.type_menage,
            type_menage_autres = EXCLUDED.type_menage_autres,
            nb_personnes = EXCLUDED.nb_personnes,
            nb_enfants_u5 = EXCLUDED.nb_enfants_u5,
            themes_sensibilisation_codes = EXCLUDED.themes_sensibilisation_codes,
            themes_sensibilisation_autres = EXCLUDED.themes_sensibilisation_autres,
            nb_seances_total = EXCLUDED.nb_seances_total,
            source_information_codes = EXCLUDED.source_information_codes,
            source_information_autres = EXCLUDED.source_information_autres,
            producteur_informe_intrants = EXCLUDED.producteur_informe_intrants,
            obs_sensib = EXCLUDED.obs_sensib,
            menage_prat_agroeco = EXCLUDED.menage_prat_agroeco,
            pratiques_agro_codes = EXCLUDED.pratiques_agro_codes,
            pratiques_agro_autres = EXCLUDED.pratiques_agro_autres,
            utilise_intrants_chimiques = EXCLUDED.utilise_intrants_chimiques,
            applique_bonnes_pratiques_intrants = EXCLUDED.applique_bonnes_pratiques_intrants,
            utilise_foyer_ameliore = EXCLUDED.utilise_foyer_ameliore,
            type_foyer_principal = EXCLUDED.type_foyer_principal,
            type_foyer_principal_autres = EXCLUDED.type_foyer_principal_autres,
            annees_utilisation_foyer = EXCLUDED.annees_utilisation_foyer,
            obs_foyer = EXCLUDED.obs_foyer,
            applique_bonnes_prat_nutrition = EXCLUDED.applique_bonnes_prat_nutrition,
            pratiques_nutrition_codes = EXCLUDED.pratiques_nutrition_codes,
            pratiques_nutrition_autres = EXCLUDED.pratiques_nutrition_autres,
            frequence_pratiques_nutrition = EXCLUDED.frequence_pratiques_nutrition,
            obs_nutrition = EXCLUDED.obs_nutrition,
            obs_menage = EXCLUDED.obs_menage,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {
                "table": "core.agr_menage",
                "affected_rows": affected,
            }
        ],
    }


def _publish_cep_parcelles_from_kobo(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("s.region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.agr_cep_raw s
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_cep), ''), '') <> ''
        """,
        params,
    )

    sql = f"""
        INSERT INTO core.cep_parcelle (
            project_code,
            id_cep,
            filiere,
            campagne_yyyy,
            id_commune,
            surface_decl,
            rendement,
            menages_beneficiaires,
            geom,
            pratiques_agroeco_codes,
            pratiques_autres,
            record_source,
            updated_at
        )
        SELECT DISTINCT ON (s.project_code, s.id_cep)
            s.project_code,
            s.id_cep,
            s.filiere,
            COALESCE(NULLIF(BTRIM(s.campagne), '')::int, EXTRACT(YEAR FROM s.submission_time)::int),
            s.commune AS id_commune,
            NULLIF(BTRIM(s.surface_ha), '')::numeric,
            COALESCE(NULLIF(BTRIM(s.rendement_calc), '')::numeric, NULLIF(BTRIM(s.rendement_saisi), '')::numeric),
            NULLIF(BTRIM(s.menages_ben), '')::int,
            CASE
                WHEN s.geom IS NULL THEN NULL
                ELSE (ST_Buffer(s.geom::geography, 5)::geometry(Polygon, 4326))
            END,
            {_split_codes_sql('s.pratiques_agroeco')},
            s.pratiques_autres,
            'kobo_agr_cep_raw',
            now()
        FROM stage.agr_cep_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_cep), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.filiere), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
        ORDER BY s.project_code, s.id_cep, s.submission_time DESC NULLS LAST, s.id DESC
        ON CONFLICT (project_code, id_cep) DO UPDATE SET
            filiere = EXCLUDED.filiere,
            campagne_yyyy = EXCLUDED.campagne_yyyy,
            id_commune = EXCLUDED.id_commune,
            surface_decl = EXCLUDED.surface_decl,
            rendement = EXCLUDED.rendement,
            menages_beneficiaires = EXCLUDED.menages_beneficiaires,
            geom = COALESCE(EXCLUDED.geom, core.cep_parcelle.geom),
            pratiques_agroeco_codes = EXCLUDED.pratiques_agroeco_codes,
            pratiques_autres = EXCLUDED.pratiques_autres,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {
                "table": "core.cep_parcelle",
                "affected_rows": affected,
            }
        ],
    }


def _bool_from_text_sql(field_sql: str) -> str:
    return (
        f"CASE WHEN {field_sql} IS NULL THEN NULL "
        f"WHEN LOWER(BTRIM({field_sql})) IN ('1','true','vrai','yes','oui') THEN TRUE "
        f"WHEN LOWER(BTRIM({field_sql})) IN ('0','false','faux','no','non') THEN FALSE "
        f"ELSE NULL END"
    )


def _publish_agr_organisations(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.agr_org_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_org), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
        """,
        params,
    )

    sql = f"""
        INSERT INTO core.agr_organisation (
            raw_uuid,
            project_code,
            id_org,
            nom_org,
            type_org,
            type_org_autres,
            statut_juridique,
            statut_autres,
            annee_creation,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            nb_membres_total,
            nb_membres_femmes,
            nb_membres_jeunes,
            activites_principales_codes,
            activites_principales_autres,
            filieres_principales_codes,
            filieres_autres,
            pratiques_adoptees,
            pratiques_agro_adoptees_codes,
            pratiques_agro_autres,
            nb_planteurs_accompagnes,
            nb_producteurs_semenciers,
            nb_banques_semences,
            nb_bovins,
            nb_ovins,
            nb_caprins,
            autres_especes_autres,
            nb_ruches_ken,
            nb_ruches_lang,
            nb_ruches_autres,
            nb_emplois_verts,
            desc_emplois_verts,
            obs_org,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT DISTINCT ON (s.project_code, s.id_org)
            s.raw_uuid,
            s.project_code,
            s.id_org,
            s.nom_org,
            s.type_org,
            s.type_org_autres,
            s.statut_juridique,
            s.statut_autres,
            s.annee_creation,
            s.commune AS id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.nb_membres_total,
            s.nb_membres_femmes,
            s.nb_membres_jeunes,
            {_split_codes_sql('s.activites_principales')},
            s.activites_principales_autres,
            {_split_codes_sql('s.filieres_principales')},
            s.filieres_autres,
            s.pratiques_adoptees,
            {_split_codes_sql('s.pratiques_agro_adoptees')},
            s.pratiques_agro_autres,
            s.nb_planteurs_accompagnes,
            s.nb_producteurs_semenciers,
            s.nb_banques_semences,
            s.nb_bovins,
            s.nb_ovins,
            s.nb_caprins,
            s.autres_especes_autres,
            s.nb_ruches_ken,
            s.nb_ruches_lang,
            s.nb_ruches_autres,
            s.nb_emplois_verts,
            s.desc_emplois_verts,
            s.obs_org,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.agr_org_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_org), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
        ORDER BY s.project_code, s.id_org, s.imported_at DESC NULLS LAST, s.raw_uuid DESC
        ON CONFLICT (project_code, id_org) DO UPDATE SET
            raw_uuid = EXCLUDED.raw_uuid,
            nom_org = EXCLUDED.nom_org,
            type_org = EXCLUDED.type_org,
            type_org_autres = EXCLUDED.type_org_autres,
            statut_juridique = EXCLUDED.statut_juridique,
            statut_autres = EXCLUDED.statut_autres,
            annee_creation = EXCLUDED.annee_creation,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            nb_membres_total = EXCLUDED.nb_membres_total,
            nb_membres_femmes = EXCLUDED.nb_membres_femmes,
            nb_membres_jeunes = EXCLUDED.nb_membres_jeunes,
            activites_principales_codes = EXCLUDED.activites_principales_codes,
            activites_principales_autres = EXCLUDED.activites_principales_autres,
            filieres_principales_codes = EXCLUDED.filieres_principales_codes,
            filieres_autres = EXCLUDED.filieres_autres,
            pratiques_adoptees = EXCLUDED.pratiques_adoptees,
            pratiques_agro_adoptees_codes = EXCLUDED.pratiques_agro_adoptees_codes,
            pratiques_agro_autres = EXCLUDED.pratiques_agro_autres,
            nb_planteurs_accompagnes = EXCLUDED.nb_planteurs_accompagnes,
            nb_producteurs_semenciers = EXCLUDED.nb_producteurs_semenciers,
            nb_banques_semences = EXCLUDED.nb_banques_semences,
            nb_bovins = EXCLUDED.nb_bovins,
            nb_ovins = EXCLUDED.nb_ovins,
            nb_caprins = EXCLUDED.nb_caprins,
            autres_especes_autres = EXCLUDED.autres_especes_autres,
            nb_ruches_ken = EXCLUDED.nb_ruches_ken,
            nb_ruches_lang = EXCLUDED.nb_ruches_lang,
            nb_ruches_autres = EXCLUDED.nb_ruches_autres,
            nb_emplois_verts = EXCLUDED.nb_emplois_verts,
            desc_emplois_verts = EXCLUDED.desc_emplois_verts,
            obs_org = EXCLUDED.obs_org,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.agr_organisation", "affected_rows": affected},
        ],
    }


def _publish_agr_comites(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.agr_comite_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_comite), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
        """,
        params,
    )

    delete_by_raw_uuid_sql = f"""
        DELETE FROM core.agr_comite c
        USING stage.agr_comite_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE c.raw_uuid = s.raw_uuid
          AND {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_comite), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
    """

    sql = f"""
        INSERT INTO core.agr_comite (
            raw_uuid,
            project_code,
            id_comite,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            type_comite,
            type_comite_autres,
            nom_comite,
            annee_creation,
            themes_comite_codes,
            themes_comite_autres,
            statut_comite,
            zone_couverture,
            nb_membres_total,
            nb_membres_femmes,
            nb_membres_jeunes,
            nb_reunions_12m,
            nb_sensib_12m,
            principaux_resultats,
            contraintes_fonctionnement,
            suit_conflits,
            nb_conflits_12m,
            nb_conflits_regles,
            types_conflits_codes,
            types_conflits_autres,
            conflits_details,
            nb_techniciens_total,
            type_techniciens_codes,
            type_techniciens_autres,
            obs_techniciens,
            obs_comite,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT DISTINCT ON (s.project_code, s.id_comite)
            s.raw_uuid,
            s.project_code,
            s.id_comite,
            s.commune AS id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.type_comite,
            s.type_comite_autres,
            s.nom_comite,
            s.annee_creation,
            {_split_codes_sql('s.themes_comite')},
            s.themes_comite_autres,
            s.statut_comite,
            s.zone_couverture,
            s.nb_membres_total,
            s.nb_membres_femmes,
            s.nb_membres_jeunes,
            s.nb_reunions_12m,
            s.nb_sensib_12m,
            s.principaux_resultats,
            s.contraintes_fonctionnement,
            s.suit_conflits,
            s.nb_conflits_12m,
            s.nb_conflits_regles,
            {_split_codes_sql('s.types_conflits')},
            s.types_conflits_autres,
            s.conflits_details,
            s.nb_techniciens_total,
            {_split_codes_sql('s.type_techniciens')},
            s.type_techniciens_autres,
            s.obs_techniciens,
            s.obs_comite,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.agr_comite_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_comite), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
        ORDER BY s.project_code, s.id_comite, s.imported_at DESC NULLS LAST, s.raw_uuid DESC
        ON CONFLICT (project_code, id_comite) DO UPDATE SET
            raw_uuid = EXCLUDED.raw_uuid,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            type_comite = EXCLUDED.type_comite,
            type_comite_autres = EXCLUDED.type_comite_autres,
            nom_comite = EXCLUDED.nom_comite,
            annee_creation = EXCLUDED.annee_creation,
            themes_comite_codes = EXCLUDED.themes_comite_codes,
            themes_comite_autres = EXCLUDED.themes_comite_autres,
            statut_comite = EXCLUDED.statut_comite,
            zone_couverture = EXCLUDED.zone_couverture,
            nb_membres_total = EXCLUDED.nb_membres_total,
            nb_membres_femmes = EXCLUDED.nb_membres_femmes,
            nb_membres_jeunes = EXCLUDED.nb_membres_jeunes,
            nb_reunions_12m = EXCLUDED.nb_reunions_12m,
            nb_sensib_12m = EXCLUDED.nb_sensib_12m,
            principaux_resultats = EXCLUDED.principaux_resultats,
            contraintes_fonctionnement = EXCLUDED.contraintes_fonctionnement,
            suit_conflits = EXCLUDED.suit_conflits,
            nb_conflits_12m = EXCLUDED.nb_conflits_12m,
            nb_conflits_regles = EXCLUDED.nb_conflits_regles,
            types_conflits_codes = EXCLUDED.types_conflits_codes,
            types_conflits_autres = EXCLUDED.types_conflits_autres,
            conflits_details = EXCLUDED.conflits_details,
            nb_techniciens_total = EXCLUDED.nb_techniciens_total,
            type_techniciens_codes = EXCLUDED.type_techniciens_codes,
            type_techniciens_autres = EXCLUDED.type_techniciens_autres,
            obs_techniciens = EXCLUDED.obs_techniciens,
            obs_comite = EXCLUDED.obs_comite,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        _exec_rowcount(delete_by_raw_uuid_sql, params)
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.agr_comite", "affected_rows": affected},
        ],
    }


def _publish_pratiques_agro(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.pratiques_agro_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
        """,
        params,
    )

    sql = f"""
        INSERT INTO core.pratiques_agro_parcelle (
            raw_uuid,
            project_code,
            id_cep,
            campagne_yyyy,
            culture_code,
            culture_autre,
            surface_ha,
            production_totale_kg,
            rendement_calc_kg_ha,
            rendement_observe_kg_ha,
            pratiques_appliquees,
            pratiques_agroeco_codes,
            pratiques_autres,
            nb_annees_pratiques,
            effet_rendement,
            effet_sols,
            obs_pratiques,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT
            s.raw_uuid,
            s.project_code,
            s.id_cep,
            s.campagne AS campagne_yyyy,
            s.culture_principale AS culture_code,
            s.culture_principale_autres AS culture_autre,
            s.surface_ha,
            s.production_totale AS production_totale_kg,
            s.rendement_calc AS rendement_calc_kg_ha,
            s.rendement_observe AS rendement_observe_kg_ha,
            {_bool_from_text_sql('s.pratiques_appliquees')},
            {_split_codes_sql('s.pratiques_agro')},
            s.pratiques_autres,
            s.nb_annees_pratiques,
            s.effet_rendement,
            s.effet_sols,
            s.obs_pratiques,
            s.id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.pratiques_agro_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
        ON CONFLICT (raw_uuid) DO UPDATE SET
            project_code = EXCLUDED.project_code,
            id_cep = EXCLUDED.id_cep,
            campagne_yyyy = EXCLUDED.campagne_yyyy,
            culture_code = EXCLUDED.culture_code,
            culture_autre = EXCLUDED.culture_autre,
            surface_ha = EXCLUDED.surface_ha,
            production_totale_kg = EXCLUDED.production_totale_kg,
            rendement_calc_kg_ha = EXCLUDED.rendement_calc_kg_ha,
            rendement_observe_kg_ha = EXCLUDED.rendement_observe_kg_ha,
            pratiques_appliquees = EXCLUDED.pratiques_appliquees,
            pratiques_agroeco_codes = EXCLUDED.pratiques_agroeco_codes,
            pratiques_autres = EXCLUDED.pratiques_autres,
            nb_annees_pratiques = EXCLUDED.nb_annees_pratiques,
            effet_rendement = EXCLUDED.effet_rendement,
            effet_sols = EXCLUDED.effet_sols,
            obs_pratiques = EXCLUDED.obs_pratiques,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.pratiques_agro_parcelle", "affected_rows": affected},
        ],
    }


def _publish_intrants_comptoirs(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.intrant_comptoir_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
        """,
        params,
    )

    intrant_sql = f"""
        INSERT INTO core.intrant_distribution (
            raw_uuid,
            project_code,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            filiere,
            type_intrant,
            intrant_autres,
            campagne_yyyy,
            quantite,
            unite_intrant,
            menages_beneficiaires,
            source_intrant,
            intrant_conforme,
            motif_non_conf,
            obs_intrant,
            valid_from,
            record_source,
            updated_at
        )
        SELECT
            s.submission_uuid AS raw_uuid,
            s.project_code,
            s.commune AS id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.filiere_intrant AS filiere,
            s.type_intrant,
            s.intrant_autres,
            s.campagne_intrant AS campagne_yyyy,
            s.quantite,
            s.unite_intrant,
            s.menages_ben_intr AS menages_beneficiaires,
            s.source_intrant,
            s.intrant_conforme,
            s.motif_non_conf,
            s.obs_intrant,
            COALESCE(s.submission_time::date, s.imported_at::date, CURRENT_DATE),
            'kobo_intrant_comptoir_raw',
            now()
        FROM stage.intrant_comptoir_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND LOWER(BTRIM(COALESCE(s.enreg_type, ''))) IN ('intrant', 'intrants')
          AND s.submission_uuid IS NOT NULL
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.filiere_intrant), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.type_intrant), ''), '') <> ''
        ON CONFLICT (raw_uuid) DO UPDATE SET
            project_code = EXCLUDED.project_code,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            filiere = EXCLUDED.filiere,
            type_intrant = EXCLUDED.type_intrant,
            intrant_autres = EXCLUDED.intrant_autres,
            campagne_yyyy = EXCLUDED.campagne_yyyy,
            quantite = EXCLUDED.quantite,
            unite_intrant = EXCLUDED.unite_intrant,
            menages_beneficiaires = EXCLUDED.menages_beneficiaires,
            source_intrant = EXCLUDED.source_intrant,
            intrant_conforme = EXCLUDED.intrant_conforme,
            motif_non_conf = EXCLUDED.motif_non_conf,
            obs_intrant = EXCLUDED.obs_intrant,
            valid_from = EXCLUDED.valid_from,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    marche_sql = f"""
        INSERT INTO core.marche (
            raw_uuid,
            project_code,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            nom_comptoir,
            filiere,
            filiere_autres,
            type_comptoir,
            frequence_marche,
            gestionnaire,
            obs_comptoir,
            valid_from,
            record_source,
            updated_at
        )
        SELECT
            s.submission_uuid AS raw_uuid,
            s.project_code,
            s.commune AS id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.nom_comptoir,
            s.filiere_comptoir AS filiere,
            s.filiere_autres,
            s.type_comptoir,
            s.frequence_marche,
            s.gestionnaire,
            s.obs_comptoir,
            COALESCE(s.submission_time::date, s.imported_at::date, CURRENT_DATE),
            'kobo_intrant_comptoir_raw',
            now()
        FROM stage.intrant_comptoir_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND LOWER(BTRIM(COALESCE(s.enreg_type, ''))) IN ('comptoir', 'marche', 'marches')
          AND s.submission_uuid IS NOT NULL
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.nom_comptoir), ''), '') <> ''
        ON CONFLICT (raw_uuid) DO UPDATE SET
            project_code = EXCLUDED.project_code,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            nom_comptoir = EXCLUDED.nom_comptoir,
            filiere = EXCLUDED.filiere,
            filiere_autres = EXCLUDED.filiere_autres,
            type_comptoir = EXCLUDED.type_comptoir,
            frequence_marche = EXCLUDED.frequence_marche,
            gestionnaire = EXCLUDED.gestionnaire,
            obs_comptoir = EXCLUDED.obs_comptoir,
            valid_from = EXCLUDED.valid_from,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        intrant_affected = _exec_rowcount(intrant_sql, params)
        marche_affected = _exec_rowcount(marche_sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.intrant_distribution", "affected_rows": intrant_affected},
            {"table": "core.marche", "affected_rows": marche_affected},
        ],
    }


def _publish_intrants_distribution(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.intrant_distribution_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.filiere), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.type_intrant), ''), '') <> ''
        """,
        params,
    )

    sql = f"""
        INSERT INTO core.intrant_distribution (
            raw_uuid,
            project_code,
            id_commune,
            id_prefecture,
            id_region,
            geom,
            filiere,
            type_intrant,
            campagne_yyyy,
            quantite,
            menages_beneficiaires,
            valid_from,
            record_source,
            updated_at
        )
        SELECT
            s.raw_uuid,
            s.project_code,
            s.id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.geom,
            s.filiere,
            s.type_intrant,
            s.campagne_yyyy,
            s.quantite,
            s.menages_beneficiaires,
            COALESCE(s.imported_at::date, CURRENT_DATE),
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.intrant_distribution_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.filiere), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.type_intrant), ''), '') <> ''
        ON CONFLICT (raw_uuid) DO UPDATE SET
            project_code = EXCLUDED.project_code,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            geom = EXCLUDED.geom,
            filiere = EXCLUDED.filiere,
            type_intrant = EXCLUDED.type_intrant,
            campagne_yyyy = EXCLUDED.campagne_yyyy,
            quantite = EXCLUDED.quantite,
            menages_beneficiaires = EXCLUDED.menages_beneficiaires,
            valid_from = EXCLUDED.valid_from,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.intrant_distribution", "affected_rows": affected},
        ],
    }


def _publish_ouvrages(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.ouvrage_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
        """,
        params,
    )

    sql = f"""
        INSERT INTO core.ouvrage (
            raw_uuid,
            project_code,
            id_ouvrage,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            type_ouvrages_codes,
            autre_ouv_preciser,
            longueur_anti_m,
            etat_anti,
            surface_couv_ha,
            etat_couv,
            obs_ouvr,
            code_ouvrage,
            record_source,
            updated_at
        )
        SELECT
            s.raw_uuid,
            s.project_code,
            COALESCE(NULLIF(BTRIM(s.code_ouvrage), ''), s.raw_uuid::text) AS id_ouvrage,
            s.commune AS id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            {_split_codes_sql('s.ouv_types')},
            s.autre_ouv_preciser,
            s.longueur_anti_m,
            s.etat_anti,
            s.surface_couv_ha,
            s.etat_couv,
            s.obs_ouvr,
            s.code_ouvrage,
            'kobo_ouvrage_raw',
            now()
        FROM stage.ouvrage_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
        ON CONFLICT (raw_uuid) DO UPDATE SET
            project_code = EXCLUDED.project_code,
            id_ouvrage = EXCLUDED.id_ouvrage,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            type_ouvrages_codes = EXCLUDED.type_ouvrages_codes,
            autre_ouv_preciser = EXCLUDED.autre_ouv_preciser,
            longueur_anti_m = EXCLUDED.longueur_anti_m,
            etat_anti = EXCLUDED.etat_anti,
            surface_couv_ha = EXCLUDED.surface_couv_ha,
            etat_couv = EXCLUDED.etat_couv,
            obs_ouvr = EXCLUDED.obs_ouvr,
            code_ouvrage = EXCLUDED.code_ouvrage,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.ouvrage", "affected_rows": affected},
        ],
    }


def _publish_couloirs(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.couloir_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_couloir), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
        """,
        params,
    )

    sql = f"""
        INSERT INTO core.couloir (
            project_code,
            id_couloir,
            nom_couloir,
            id_commune,
            id_prefecture,
            id_region,
            geom,
            type_couloir,
            longueur_km,
            largeur_m,
            especes_codes,
            especes_autres,
            saison_usage,
            saison_usage_autres,
            statut_couloir,
            localites_traversees,
            infra_codes,
            infra_autres,
            types_conflits_codes,
            types_conflits_autres,
            conflits_details,
            appreciation_globale,
            obs_couloir,
            valid_from,
            record_source,
            updated_at
        )
        SELECT DISTINCT ON (s.project_code, s.id_couloir)
            s.project_code,
            s.id_couloir,
            s.nom_couloir,
            s.commune AS id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.geom,
            s.type_couloir,
            s.longueur_km,
            s.largeur_m,
            {_split_codes_sql('s.especes_troupeaux')},
            s.especes_troupeaux_autres,
            s.saison_usage,
            s.saison_usage_autres,
            s.statut_couloir,
            s.localites_traversees,
            {_split_codes_sql('s.infra_exist')},
            s.infra_autres,
            {_split_codes_sql('s.types_conflits')},
            s.types_conflits_autres,
            s.conflits_details,
            s.appreciation_globale,
            s.obs_couloir,
            COALESCE(s.submission_time::date, s.imported_at::date, CURRENT_DATE),
            'kobo_couloir_raw',
            now()
        FROM stage.couloir_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_couloir), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
        ORDER BY s.project_code, s.id_couloir, s.submission_time DESC NULLS LAST, s.raw_uuid DESC
        ON CONFLICT (project_code, id_couloir) DO UPDATE SET
            nom_couloir = EXCLUDED.nom_couloir,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            geom = EXCLUDED.geom,
            type_couloir = EXCLUDED.type_couloir,
            longueur_km = EXCLUDED.longueur_km,
            largeur_m = EXCLUDED.largeur_m,
            especes_codes = EXCLUDED.especes_codes,
            especes_autres = EXCLUDED.especes_autres,
            saison_usage = EXCLUDED.saison_usage,
            saison_usage_autres = EXCLUDED.saison_usage_autres,
            statut_couloir = EXCLUDED.statut_couloir,
            localites_traversees = EXCLUDED.localites_traversees,
            infra_codes = EXCLUDED.infra_codes,
            infra_autres = EXCLUDED.infra_autres,
            types_conflits_codes = EXCLUDED.types_conflits_codes,
            types_conflits_autres = EXCLUDED.types_conflits_autres,
            conflits_details = EXCLUDED.conflits_details,
            appreciation_globale = EXCLUDED.appreciation_globale,
            obs_couloir = EXCLUDED.obs_couloir,
            valid_from = EXCLUDED.valid_from,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.couloir", "affected_rows": affected},
        ],
    }


def _publish_meteo_stations(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.meteo_station_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.code_station), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        """,
        params,
    )

    station_sql = f"""
        INSERT INTO core.meteo_station (
            raw_uuid,
            project_code,
            code_station,
            nom_station,
            type_station,
            type_station_autres,
            proprietaire,
            proprietaire_autres,
            statut_station,
            date_mise_service,
            frequence_mesure,
            type_releve,
            etat_equipements,
            obs_station,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT DISTINCT ON (s.project_code, s.code_station)
            s.raw_uuid,
            s.project_code,
            s.code_station,
            s.nom_station,
            s.type_station,
            s.type_station_autres,
            s.proprietaire,
            s.proprietaire_autres,
            s.statut_station,
            s.date_mise_service,
            s.frequence_mesure,
            s.type_releve,
            s.etat_equipements,
            s.obs_station,
            s.id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.meteo_station_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.code_station), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        ORDER BY s.project_code, s.code_station, s.imported_at DESC NULLS LAST, s.raw_uuid DESC
        ON CONFLICT (project_code, code_station) DO UPDATE SET
            raw_uuid = EXCLUDED.raw_uuid,
            nom_station = EXCLUDED.nom_station,
            type_station = EXCLUDED.type_station,
            type_station_autres = EXCLUDED.type_station_autres,
            proprietaire = EXCLUDED.proprietaire,
            proprietaire_autres = EXCLUDED.proprietaire_autres,
            statut_station = EXCLUDED.statut_station,
            date_mise_service = EXCLUDED.date_mise_service,
            frequence_mesure = EXCLUDED.frequence_mesure,
            type_releve = EXCLUDED.type_releve,
            etat_equipements = EXCLUDED.etat_equipements,
            obs_station = EXCLUDED.obs_station,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    mesure_sql = """
        INSERT INTO core.meteo_mesure (
            raw_uuid,
            project_code,
            station_uuid,
            code_station,
            date_obs,
            pluie_mm,
            t_min,
            t_max,
            obs_pluie,
            id_commune,
            id_prefecture,
            id_region,
            geom,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT
            s.raw_uuid,
            s.project_code,
            st.station_uuid,
            s.code_station,
            s.date_obs,
            s.pluie_mm,
            s.t_min,
            s.t_max,
            s.obs_pluie,
            st.id_commune,
            st.id_prefecture,
            st.id_region,
            st.geom,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            'kobo_meteo_station_raw',
            now()
        FROM stage.meteo_station_raw s
        JOIN core.meteo_station st
          ON st.project_code = s.project_code
         AND st.code_station = s.code_station
        WHERE s.project_code = %s
          AND s.date_obs IS NOT NULL
          AND COALESCE(NULLIF(BTRIM(s.code_station), ''), '') <> ''
        ON CONFLICT (raw_uuid) DO UPDATE SET
            project_code = EXCLUDED.project_code,
            station_uuid = EXCLUDED.station_uuid,
            code_station = EXCLUDED.code_station,
            date_obs = EXCLUDED.date_obs,
            pluie_mm = EXCLUDED.pluie_mm,
            t_min = EXCLUDED.t_min,
            t_max = EXCLUDED.t_max,
            obs_pluie = EXCLUDED.obs_pluie,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            geom = EXCLUDED.geom,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        station_affected = _exec_rowcount(station_sql, params)
        mesure_affected = _exec_rowcount(mesure_sql, [project_code])

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.meteo_station", "affected_rows": station_affected},
            {"table": "core.meteo_mesure", "affected_rows": mesure_affected},
        ],
    }


def _publish_meteo_mesures(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    stage_count = _count_scalar(
        """
        SELECT COUNT(*)
        FROM stage.meteo_mesure_raw s
        WHERE s.project_code = %s
        """,
        [project_code],
    )

    sql = """
        INSERT INTO core.meteo_mesure (
            raw_uuid,
            project_code,
            station_uuid,
            code_station,
            date_obs,
            pluie_mm,
            t_min,
            t_max,
            id_commune,
            id_prefecture,
            id_region,
            geom,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT
            s.raw_uuid,
            s.project_code,
            st.station_uuid,
            s.code_station,
            s.ts_obs::date,
            s.pluie_mm,
            s.t_min,
            s.t_max,
            st.id_commune,
            st.id_prefecture,
            st.id_region,
            st.geom,
            s.raw_payload,
            s.import_source,
            s.import_batch::text,
            s.imported_at,
            true,
            'stage_meteo_mesure_raw',
            now()
        FROM stage.meteo_mesure_raw s
        JOIN core.meteo_station st
          ON st.project_code = s.project_code
         AND st.code_station = s.code_station
        WHERE s.project_code = %s
          AND s.raw_uuid IS NOT NULL
        ON CONFLICT (raw_uuid) DO UPDATE SET
            project_code = EXCLUDED.project_code,
            station_uuid = EXCLUDED.station_uuid,
            code_station = EXCLUDED.code_station,
            date_obs = EXCLUDED.date_obs,
            pluie_mm = EXCLUDED.pluie_mm,
            t_min = EXCLUDED.t_min,
            t_max = EXCLUDED.t_max,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            geom = EXCLUDED.geom,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, [project_code])

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.meteo_mesure", "affected_rows": affected},
        ],
    }


def _publish_tetes_sources(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.tete_source_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_ts), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        """,
        params,
    )

    sql = f"""
        INSERT INTO core.tete_source (
            raw_uuid,
            project_code,
            id_ts,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            type_source,
            type_source_autres,
            usage_principal,
            pop_desservie,
            protection_exist,
            type_protection_codes,
            protections_autres,
            etat_fonctionnel,
            annee_protection,
            entretien_regulier,
            resp_entretien,
            obs_ts,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT DISTINCT ON (s.project_code, s.id_ts)
            s.raw_uuid,
            s.project_code,
            s.id_ts,
            s.id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.type_source,
            s.type_source_autres,
            s.usage_principal,
            s.pop_desservie,
            s.protection_exist,
            {_split_codes_sql('s.type_protection')},
            s.protections_autres,
            s.etat_fonctionnel,
            s.annee_protection,
            s.entretien_regulier,
            s.resp_entretien,
            s.obs_ts,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.tete_source_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_ts), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        ORDER BY s.project_code, s.id_ts, s.imported_at DESC NULLS LAST, s.raw_uuid DESC
        ON CONFLICT (project_code, id_ts) DO UPDATE SET
            raw_uuid = EXCLUDED.raw_uuid,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            type_source = EXCLUDED.type_source,
            type_source_autres = EXCLUDED.type_source_autres,
            usage_principal = EXCLUDED.usage_principal,
            pop_desservie = EXCLUDED.pop_desservie,
            protection_exist = EXCLUDED.protection_exist,
            type_protection_codes = EXCLUDED.type_protection_codes,
            protections_autres = EXCLUDED.protections_autres,
            etat_fonctionnel = EXCLUDED.etat_fonctionnel,
            annee_protection = EXCLUDED.annee_protection,
            entretien_regulier = EXCLUDED.entretien_regulier,
            resp_entretien = EXCLUDED.resp_entretien,
            obs_ts = EXCLUDED.obs_ts,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.tete_source", "affected_rows": affected},
        ],
    }


def _publish_zones_degradees(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.zone_degradee_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_zone), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        """,
        params,
    )

    sql = f"""
        INSERT INTO core.zone_degradee (
            raw_uuid,
            project_code,
            id_zone,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom_point,
            geom_zone,
            zone_degrad_pres,
            type_degradation,
            severite,
            surface_degrad_ha,
            cause_detail,
            obs_degrad,
            restauration_real,
            type_intervention_codes,
            autre_interv_prec,
            surface_restaur_ha,
            nb_plants,
            densite_plants_ha,
            especes_codes,
            especes_autres,
            annee_plantation,
            suivi_plantation,
            taux_survie_pct,
            surf_regen_ha,
            pratiques_regen,
            nb_terrasses,
            longueur_terr_m,
            autre_interv_descr,
            etat_restaur,
            obs_restaur,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT DISTINCT ON (s.project_code, s.id_zone)
            s.raw_uuid,
            s.project_code,
            s.id_zone,
            s.id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom_point,
            s.geom_zone,
            s.zone_degrad_pres,
            s.type_degradation,
            s.severite,
            s.surface_degrad_ha,
            s.cause_detail,
            s.obs_degrad,
            s.restauration_real,
            {_split_codes_sql('s.type_intervention')},
            s.autre_interv_prec,
            s.surface_restaur_ha,
            s.nb_plants,
            s.densite_plants_ha,
            {_split_codes_sql('s.especes')},
            s.especes_autres,
            s.annee_plantation,
            s.suivi_plantation,
            s.taux_survie_pct,
            s.surf_regen_ha,
            s.pratiques_regen,
            s.nb_terrasses,
            s.longueur_terr_m,
            s.autre_interv_descr,
            s.etat_restaur,
            s.obs_restaur,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.zone_degradee_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_zone), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        ORDER BY s.project_code, s.id_zone, s.imported_at DESC NULLS LAST, s.raw_uuid DESC
        ON CONFLICT (project_code, id_zone) DO UPDATE SET
            raw_uuid = EXCLUDED.raw_uuid,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom_point = EXCLUDED.geom_point,
            geom_zone = EXCLUDED.geom_zone,
            zone_degrad_pres = EXCLUDED.zone_degrad_pres,
            type_degradation = EXCLUDED.type_degradation,
            severite = EXCLUDED.severite,
            surface_degrad_ha = EXCLUDED.surface_degrad_ha,
            cause_detail = EXCLUDED.cause_detail,
            obs_degrad = EXCLUDED.obs_degrad,
            restauration_real = EXCLUDED.restauration_real,
            type_intervention_codes = EXCLUDED.type_intervention_codes,
            autre_interv_prec = EXCLUDED.autre_interv_prec,
            surface_restaur_ha = EXCLUDED.surface_restaur_ha,
            nb_plants = EXCLUDED.nb_plants,
            densite_plants_ha = EXCLUDED.densite_plants_ha,
            especes_codes = EXCLUDED.especes_codes,
            especes_autres = EXCLUDED.especes_autres,
            annee_plantation = EXCLUDED.annee_plantation,
            suivi_plantation = EXCLUDED.suivi_plantation,
            taux_survie_pct = EXCLUDED.taux_survie_pct,
            surf_regen_ha = EXCLUDED.surf_regen_ha,
            pratiques_regen = EXCLUDED.pratiques_regen,
            nb_terrasses = EXCLUDED.nb_terrasses,
            longueur_terr_m = EXCLUDED.longueur_terr_m,
            autre_interv_descr = EXCLUDED.autre_interv_descr,
            etat_restaur = EXCLUDED.etat_restaur,
            obs_restaur = EXCLUDED.obs_restaur,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.zone_degradee", "affected_rows": affected},
        ],
    }


def _publish_fiere_suivi_sortants(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.fiere_suivi_sortant_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_sortant), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
        """,
        params,
    )

    delete_by_raw_uuid_sql = f"""
        DELETE FROM core.fiere_suivi_sortant c
        USING stage.fiere_suivi_sortant_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE c.raw_uuid = s.raw_uuid
          AND {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_sortant), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
    """

    sql = f"""
        INSERT INTO core.fiere_suivi_sortant (
            raw_uuid,
            project_code,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            id_formation,
            intitule_formation,
            centre_formation,
            date_fin_formation,
            filiere_principale,
            filiere_principale_autres,
            domaine_formation,
            domaine_formation_autres,
            id_sortant,
            nom_sortant,
            sexe,
            age,
            pvh,
            niveau_etude,
            telephone,
            periode_suivi,
            periode_suivi_autres,
            date_suivi,
            insere,
            type_insertion,
            type_insertion_autres,
            domaine_emploi,
            domaine_emploi_autres,
            employeur_ou_activite,
            emploi_en_lien_formation,
            revenu_mensuel,
            satisfaction_insertion,
            raison_non_insertion,
            obs_suivi,
            obs_generales,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT DISTINCT ON (s.project_code, s.id_sortant, s.periode_suivi)
            s.raw_uuid,
            s.project_code,
            s.commune AS id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.id_formation,
            s.intitule_formation,
            s.centre_formation,
            s.date_fin_formation,
            s.filiere_principale,
            s.filiere_principale_autres,
            s.domaine_formation,
            s.domaine_formation_autres,
            s.id_sortant,
            s.nom_sortant,
            s.sexe,
            s.age,
            s.pvh,
            s.niveau_etude,
            s.telephone,
            s.periode_suivi,
            s.periode_suivi_autres,
            s.date_suivi,
            s.insere,
            s.type_insertion,
            s.type_insertion_autres,
            s.domaine_emploi,
            s.domaine_emploi_autres,
            s.employeur_ou_activite,
            s.emploi_en_lien_formation,
            s.revenu_mensuel,
            s.satisfaction_insertion,
            s.raison_non_insertion,
            s.obs_suivi,
            s.obs_generales,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.fiere_suivi_sortant_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_sortant), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.commune), ''), '') <> ''
        ORDER BY s.project_code, s.id_sortant, s.periode_suivi, s.imported_at DESC NULLS LAST, s.raw_uuid DESC
        ON CONFLICT (project_code, id_sortant, periode_suivi) DO UPDATE SET
            raw_uuid = EXCLUDED.raw_uuid,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            id_formation = EXCLUDED.id_formation,
            intitule_formation = EXCLUDED.intitule_formation,
            centre_formation = EXCLUDED.centre_formation,
            date_fin_formation = EXCLUDED.date_fin_formation,
            filiere_principale = EXCLUDED.filiere_principale,
            filiere_principale_autres = EXCLUDED.filiere_principale_autres,
            domaine_formation = EXCLUDED.domaine_formation,
            domaine_formation_autres = EXCLUDED.domaine_formation_autres,
            nom_sortant = EXCLUDED.nom_sortant,
            sexe = EXCLUDED.sexe,
            age = EXCLUDED.age,
            pvh = EXCLUDED.pvh,
            niveau_etude = EXCLUDED.niveau_etude,
            telephone = EXCLUDED.telephone,
            periode_suivi_autres = EXCLUDED.periode_suivi_autres,
            date_suivi = EXCLUDED.date_suivi,
            insere = EXCLUDED.insere,
            type_insertion = EXCLUDED.type_insertion,
            type_insertion_autres = EXCLUDED.type_insertion_autres,
            domaine_emploi = EXCLUDED.domaine_emploi,
            domaine_emploi_autres = EXCLUDED.domaine_emploi_autres,
            employeur_ou_activite = EXCLUDED.employeur_ou_activite,
            emploi_en_lien_formation = EXCLUDED.emploi_en_lien_formation,
            revenu_mensuel = EXCLUDED.revenu_mensuel,
            satisfaction_insertion = EXCLUDED.satisfaction_insertion,
            raison_non_insertion = EXCLUDED.raison_non_insertion,
            obs_suivi = EXCLUDED.obs_suivi,
            obs_generales = EXCLUDED.obs_generales,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        _exec_rowcount(delete_by_raw_uuid_sql, params)
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.fiere_suivi_sortant", "affected_rows": affected},
        ],
    }


def _publish_fiere_entreprises(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.fiere_entreprise_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_ent), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        """,
        params,
    )

    sql = f"""
        INSERT INTO core.entreprise_econ (
            raw_uuid,
            project_code,
            id_ent,
            raison_sociale,
            nom_commercial,
            statut_juridique,
            statut_juridique_autres,
            annee_creation,
            forme_propriete,
            forme_propriete_autres,
            secteur_principal,
            secteur_principal_autres,
            secteurs_secondaires_codes,
            secteurs_secondaires_autres,
            activite_detaillee,
            taille_entreprise,
            effectif_total,
            ca_approx,
            marche_principal,
            enregistre_formel,
            num_registre,
            nom_responsable,
            contact_telephon,
            contact_email,
            mpme_appuyee_fiere,
            type_appui,
            type_appui_autres,
            obs_entreprise,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT DISTINCT ON (s.project_code, s.id_ent)
            s.raw_uuid,
            s.project_code,
            s.id_ent,
            s.raison_sociale,
            s.nom_commercial,
            s.statut_juridique,
            s.statut_juridique_autres,
            s.annee_creation,
            s.forme_propriete,
            s.forme_propriete_autres,
            s.secteur_principal,
            s.secteur_principal_autres,
            {_split_codes_sql('s.secteurs_secondaires')},
            s.secteurs_secondaires_autres,
            s.activite_detaillee,
            s.taille_entreprise,
            s.effectif_total,
            s.ca_approx,
            s.marche_principal,
            s.enregistre_formel,
            s.num_registre,
            s.nom_responsable,
            s.contact_telephon,
            s.contact_email,
            s.mpme_appuyee_fiere,
            s.type_appui,
            s.type_appui_autres,
            s.obs_entreprise,
            s.id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.fiere_entreprise_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_ent), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        ORDER BY s.project_code, s.id_ent, s.imported_at DESC NULLS LAST, s.raw_uuid DESC
        ON CONFLICT (project_code, id_ent) DO UPDATE SET
            raw_uuid = EXCLUDED.raw_uuid,
            raison_sociale = EXCLUDED.raison_sociale,
            nom_commercial = EXCLUDED.nom_commercial,
            statut_juridique = EXCLUDED.statut_juridique,
            statut_juridique_autres = EXCLUDED.statut_juridique_autres,
            annee_creation = EXCLUDED.annee_creation,
            forme_propriete = EXCLUDED.forme_propriete,
            forme_propriete_autres = EXCLUDED.forme_propriete_autres,
            secteur_principal = EXCLUDED.secteur_principal,
            secteur_principal_autres = EXCLUDED.secteur_principal_autres,
            secteurs_secondaires_codes = EXCLUDED.secteurs_secondaires_codes,
            secteurs_secondaires_autres = EXCLUDED.secteurs_secondaires_autres,
            activite_detaillee = EXCLUDED.activite_detaillee,
            taille_entreprise = EXCLUDED.taille_entreprise,
            effectif_total = EXCLUDED.effectif_total,
            ca_approx = EXCLUDED.ca_approx,
            marche_principal = EXCLUDED.marche_principal,
            enregistre_formel = EXCLUDED.enregistre_formel,
            num_registre = EXCLUDED.num_registre,
            nom_responsable = EXCLUDED.nom_responsable,
            contact_telephon = EXCLUDED.contact_telephon,
            contact_email = EXCLUDED.contact_email,
            mpme_appuyee_fiere = EXCLUDED.mpme_appuyee_fiere,
            type_appui = EXCLUDED.type_appui,
            type_appui_autres = EXCLUDED.type_appui_autres,
            obs_entreprise = EXCLUDED.obs_entreprise,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.entreprise_econ", "affected_rows": affected},
        ],
    }


def _publish_fiere_formations(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.fiere_formation_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_formation), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        """,
        params,
    )

    formation_sql = f"""
        INSERT INTO core.formation_eco (
            raw_uuid,
            project_code,
            formation_liee_ent,
            id_ent,
            raison_sociale,
            org_beneficiaire,
            id_formation,
            intitule_formation,
            organisme_formateur,
            type_formation,
            type_formation_autres,
            modalite_formation,
            date_debut,
            date_fin,
            duree_jours,
            filiere_principale,
            filiere_principale_autres,
            domaine_formation,
            domaine_formation_autres,
            participants_total,
            participants_femmes,
            participants_jeunes,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at,
            participants_pvh,
            participants_pvh_femmes,
            participants_pvh_jeunes,
            participants_inscrits,
            participants_acheve
        )
        SELECT DISTINCT ON (s.project_code, s.id_formation)
            s.raw_uuid,
            s.project_code,
            s.formation_liee_ent,
            s.id_ent,
            s.raison_sociale,
            s.org_beneficiaire,
            s.id_formation,
            s.intitule_formation,
            s.organisme_formateur,
            s.type_formation,
            s.type_formation_autres,
            s.modalite_formation,
            s.date_debut,
            s.date_fin,
            s.duree_jours,
            s.filiere_principale,
            s.filiere_principale_autres,
            s.domaine_formation,
            s.domaine_formation_autres,
            s.participants_total,
            s.participants_femmes,
            s.participants_jeunes,
            s.id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now(),
            s.participants_pvh,
            s.participants_pvh_femmes,
            s.participants_pvh_jeunes,
            s.participants_inscrits,
            s.participants_acheve
        FROM stage.fiere_formation_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_formation), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        ORDER BY s.project_code, s.id_formation, s.imported_at DESC NULLS LAST, s.raw_uuid DESC
        ON CONFLICT (project_code, id_formation) DO UPDATE SET
            raw_uuid = EXCLUDED.raw_uuid,
            formation_liee_ent = EXCLUDED.formation_liee_ent,
            id_ent = EXCLUDED.id_ent,
            raison_sociale = EXCLUDED.raison_sociale,
            org_beneficiaire = EXCLUDED.org_beneficiaire,
            intitule_formation = EXCLUDED.intitule_formation,
            organisme_formateur = EXCLUDED.organisme_formateur,
            type_formation = EXCLUDED.type_formation,
            type_formation_autres = EXCLUDED.type_formation_autres,
            modalite_formation = EXCLUDED.modalite_formation,
            date_debut = EXCLUDED.date_debut,
            date_fin = EXCLUDED.date_fin,
            duree_jours = EXCLUDED.duree_jours,
            filiere_principale = EXCLUDED.filiere_principale,
            filiere_principale_autres = EXCLUDED.filiere_principale_autres,
            domaine_formation = EXCLUDED.domaine_formation,
            domaine_formation_autres = EXCLUDED.domaine_formation_autres,
            participants_total = EXCLUDED.participants_total,
            participants_femmes = EXCLUDED.participants_femmes,
            participants_jeunes = EXCLUDED.participants_jeunes,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now(),
            participants_pvh = EXCLUDED.participants_pvh,
            participants_pvh_femmes = EXCLUDED.participants_pvh_femmes,
            participants_pvh_jeunes = EXCLUDED.participants_pvh_jeunes,
            participants_inscrits = EXCLUDED.participants_inscrits,
            participants_acheve = EXCLUDED.participants_acheve
    """

    placeholder_delete_filters = ["f.project_code = %s", "pc.record_source = 'import_placeholder_total'"]
    placeholder_delete_params: list[Any] = [project_code]
    if region_id:
        placeholder_delete_filters.append("f.id_region = %s")
        placeholder_delete_params.append(region_id)

    placeholder_delete_sql = f"""
        DELETE FROM core.formation_part_cat pc
        USING core.formation_eco f
        WHERE pc.formation_uuid = f.formation_uuid
          AND {" AND ".join(placeholder_delete_filters)}
    """

    placeholder_insert_filters = ["f.project_code = %s"]
    placeholder_insert_params: list[Any] = [project_code]
    if region_id:
        placeholder_insert_filters.append("f.id_region = %s")
        placeholder_insert_params.append(region_id)

    placeholder_insert_sql = f"""
        INSERT INTO core.formation_part_cat (
            formation_uuid,
            categorie_code,
            categorie_autre,
            nb_part_cat,
            nb_part_fem_cat,
            nb_part_jeunes_cat,
            record_source,
            updated_at
        )
        SELECT
            f.formation_uuid,
            NULL,
            NULL,
            f.participants_total,
            f.participants_femmes,
            f.participants_jeunes,
            'import_placeholder_total',
            now()
        FROM core.formation_eco f
        WHERE {" AND ".join(placeholder_insert_filters)}
          AND NOT EXISTS (
              SELECT 1 FROM core.formation_part_cat pc2 WHERE pc2.formation_uuid = f.formation_uuid
          )
    """

    with transaction.atomic():
        formation_affected = _exec_rowcount(formation_sql, params)
        _exec_rowcount(placeholder_delete_sql, placeholder_delete_params)
        placeholder_affected = _exec_rowcount(placeholder_insert_sql, placeholder_insert_params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.formation_eco", "affected_rows": formation_affected},
            {"table": "core.formation_part_cat", "affected_rows": placeholder_affected},
        ],
    }


def _publish_fiere_emploi_insertion(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.fiere_empins_ent_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_ent), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        """,
        params,
    )

    emploi_sql = f"""
        INSERT INTO core.ent_emploi (
            raw_uuid,
            project_code,
            id_ent,
            raison_sociale,
            annee_ref,
            periode_ref,
            periode_ref_autres,
            emplois_total,
            emplois_femmes,
            emplois_jeunes,
            obs_emploi_ins,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT
            s.raw_uuid,
            s.project_code,
            s.id_ent,
            s.raison_sociale,
            s.annee_ref,
            s.periode_ref,
            s.periode_ref_autres,
            s.emplois_total,
            s.emplois_femmes,
            s.emplois_jeunes,
            s.obs_emploi_ins,
            s.id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.fiere_empins_ent_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_ent), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        ON CONFLICT (raw_uuid) DO UPDATE SET
            project_code = EXCLUDED.project_code,
            id_ent = EXCLUDED.id_ent,
            raison_sociale = EXCLUDED.raison_sociale,
            annee_ref = EXCLUDED.annee_ref,
            periode_ref = EXCLUDED.periode_ref,
            periode_ref_autres = EXCLUDED.periode_ref_autres,
            emplois_total = EXCLUDED.emplois_total,
            emplois_femmes = EXCLUDED.emplois_femmes,
            emplois_jeunes = EXCLUDED.emplois_jeunes,
            obs_emploi_ins = EXCLUDED.obs_emploi_ins,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    insertion_sql = f"""
        INSERT INTO core.ent_insertion (
            raw_uuid,
            project_code,
            id_ent,
            raison_sociale,
            annee_ref,
            periode_ref,
            periode_ref_autres,
            insert_total,
            insert_femmes,
            insert_jeunes,
            obs_emploi_ins,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT
            s.raw_uuid,
            s.project_code,
            s.id_ent,
            s.raison_sociale,
            s.annee_ref,
            s.periode_ref,
            s.periode_ref_autres,
            s.insert_total,
            s.insert_femmes,
            s.insert_jeunes,
            s.obs_emploi_ins,
            s.id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.fiere_empins_ent_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_ent), ''), '') <> ''
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        ON CONFLICT (raw_uuid) DO UPDATE SET
            project_code = EXCLUDED.project_code,
            id_ent = EXCLUDED.id_ent,
            raison_sociale = EXCLUDED.raison_sociale,
            annee_ref = EXCLUDED.annee_ref,
            periode_ref = EXCLUDED.periode_ref,
            periode_ref_autres = EXCLUDED.periode_ref_autres,
            insert_total = EXCLUDED.insert_total,
            insert_femmes = EXCLUDED.insert_femmes,
            insert_jeunes = EXCLUDED.insert_jeunes,
            obs_emploi_ins = EXCLUDED.obs_emploi_ins,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    placeholder_delete_filters = ["e.project_code = %s", "d.record_source = 'import_placeholder_total'"]
    placeholder_delete_params: list[Any] = [project_code]
    if region_id:
        placeholder_delete_filters.append("e.id_region = %s")
        placeholder_delete_params.append(region_id)

    emploi_dom_delete_sql = f"""
        DELETE FROM core.ent_emploi_dom d
        USING core.ent_emploi e
        WHERE d.emploi_uuid = e.emploi_uuid
          AND {" AND ".join(placeholder_delete_filters)}
    """

    insertion_dom_delete_sql = f"""
        DELETE FROM core.ent_insertion_dom d
        USING core.ent_insertion e
        WHERE d.insertion_uuid = e.insertion_uuid
          AND {" AND ".join(placeholder_delete_filters)}
    """

    placeholder_insert_filters = ["e.project_code = %s"]
    placeholder_insert_params: list[Any] = [project_code]
    if region_id:
        placeholder_insert_filters.append("e.id_region = %s")
        placeholder_insert_params.append(region_id)

    emploi_dom_insert_sql = f"""
        INSERT INTO core.ent_emploi_dom (
            emploi_uuid,
            domaine_code,
            domaine_autre,
            nb_empl_dom,
            nb_empl_fem_dom,
            nb_empl_jeunes_dom,
            record_source,
            updated_at
        )
        SELECT
            e.emploi_uuid,
            NULL,
            NULL,
            e.emplois_total,
            e.emplois_femmes,
            e.emplois_jeunes,
            'import_placeholder_total',
            now()
        FROM core.ent_emploi e
        WHERE {" AND ".join(placeholder_insert_filters)}
          AND NOT EXISTS (
              SELECT 1 FROM core.ent_emploi_dom d2 WHERE d2.emploi_uuid = e.emploi_uuid
          )
    """

    insertion_dom_insert_sql = f"""
        INSERT INTO core.ent_insertion_dom (
            insertion_uuid,
            domaine_code,
            domaine_autre,
            nb_ins_dom,
            nb_ins_fem_dom,
            nb_ins_jeunes_dom,
            record_source,
            updated_at
        )
        SELECT
            e.insertion_uuid,
            NULL,
            NULL,
            e.insert_total,
            e.insert_femmes,
            e.insert_jeunes,
            'import_placeholder_total',
            now()
        FROM core.ent_insertion e
        WHERE {" AND ".join(placeholder_insert_filters)}
          AND NOT EXISTS (
              SELECT 1 FROM core.ent_insertion_dom d2 WHERE d2.insertion_uuid = e.insertion_uuid
          )
    """

    with transaction.atomic():
        emploi_affected = _exec_rowcount(emploi_sql, params)
        insertion_affected = _exec_rowcount(insertion_sql, params)
        _exec_rowcount(emploi_dom_delete_sql, placeholder_delete_params)
        _exec_rowcount(insertion_dom_delete_sql, placeholder_delete_params)
        emploi_dom_affected = _exec_rowcount(emploi_dom_insert_sql, placeholder_insert_params)
        insertion_dom_affected = _exec_rowcount(insertion_dom_insert_sql, placeholder_insert_params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.ent_emploi", "affected_rows": emploi_affected},
            {"table": "core.ent_insertion", "affected_rows": insertion_affected},
            {"table": "core.ent_emploi_dom", "affected_rows": emploi_dom_affected},
            {"table": "core.ent_insertion_dom", "affected_rows": insertion_dom_affected},
        ],
    }


def _publish_fiere_emploi_domaines(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["e.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("e.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.fiere_emploi_dom_raw s
        JOIN core.ent_emploi e ON e.raw_uuid = s.raw_uuid
        WHERE {where_sql}
          AND (
              COALESCE(NULLIF(BTRIM(s.domaine_emploi), ''), '') <> ''
              OR COALESCE(NULLIF(BTRIM(s.domaine_emploi_autres), ''), '') <> ''
          )
        """,
        params,
    )

    delete_sql = f"""
        DELETE FROM core.ent_emploi_dom d
        USING core.ent_emploi e
        WHERE d.emploi_uuid = e.emploi_uuid
          AND {where_sql}
    """

    insert_sql = f"""
        INSERT INTO core.ent_emploi_dom (
            emploi_uuid,
            domaine_code,
            domaine_autre,
            nb_empl_dom,
            nb_empl_fem_dom,
            nb_empl_jeunes_dom,
            nb_empl_pvh_dom,
            emploi_vert_dom,
            valid_from,
            record_source,
            updated_at
        )
        SELECT DISTINCT
            e.emploi_uuid,
            NULLIF(BTRIM(s.domaine_emploi), ''),
            NULLIF(BTRIM(s.domaine_emploi_autres), ''),
            s.nb_empl_dom,
            s.nb_empl_fem_dom,
            s.nb_empl_jeunes_dom,
            s.nb_empl_pvh_dom,
            NULLIF(BTRIM(s.emploi_vert_dom), ''),
            COALESCE(e.valid_from, CURRENT_DATE),
            'admin_csv',
            now()
        FROM stage.fiere_emploi_dom_raw s
        JOIN core.ent_emploi e ON e.raw_uuid = s.raw_uuid
        WHERE {where_sql}
          AND (
              COALESCE(NULLIF(BTRIM(s.domaine_emploi), ''), '') <> ''
              OR COALESCE(NULLIF(BTRIM(s.domaine_emploi_autres), ''), '') <> ''
          )
    """

    with transaction.atomic():
        _exec_rowcount(delete_sql, params)
        affected = _exec_rowcount(insert_sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.ent_emploi_dom", "affected_rows": affected},
        ],
    }


def _publish_fiere_insertion_domaines(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["e.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("e.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.fiere_insertion_dom_raw s
        JOIN core.ent_insertion e ON e.raw_uuid = s.raw_uuid
        WHERE {where_sql}
          AND (
              COALESCE(NULLIF(BTRIM(s.domaine_insertion), ''), '') <> ''
              OR COALESCE(NULLIF(BTRIM(s.domaine_insertion_autres), ''), '') <> ''
              OR COALESCE(NULLIF(BTRIM(s.type_insertion), ''), '') <> ''
              OR COALESCE(NULLIF(BTRIM(s.type_insertion_autres), ''), '') <> ''
          )
        """,
        params,
    )

    delete_sql = f"""
        DELETE FROM core.ent_insertion_dom d
        USING core.ent_insertion e
        WHERE d.insertion_uuid = e.insertion_uuid
          AND {where_sql}
    """

    insert_sql = f"""
        INSERT INTO core.ent_insertion_dom (
            insertion_uuid,
            domaine_code,
            domaine_autre,
            type_insertion_code,
            type_insertion_autres,
            nb_ins_dom,
            nb_ins_fem_dom,
            nb_ins_jeunes_dom,
            duree_insertion_mois,
            nb_ins_pvh_dom,
            insertion_verte_dom,
            valid_from,
            record_source,
            updated_at
        )
        SELECT DISTINCT
            e.insertion_uuid,
            NULLIF(BTRIM(s.domaine_insertion), ''),
            NULLIF(BTRIM(s.domaine_insertion_autres), ''),
            NULLIF(BTRIM(s.type_insertion), ''),
            NULLIF(BTRIM(s.type_insertion_autres), ''),
            s.nb_ins_dom,
            s.nb_ins_fem_dom,
            s.nb_ins_jeunes_dom,
            s.duree_insertion_mois,
            s.nb_ins_pvh_dom,
            NULLIF(BTRIM(s.insertion_verte_dom), ''),
            COALESCE(e.valid_from, CURRENT_DATE),
            'admin_csv',
            now()
        FROM stage.fiere_insertion_dom_raw s
        JOIN core.ent_insertion e ON e.raw_uuid = s.raw_uuid
        WHERE {where_sql}
          AND (
              COALESCE(NULLIF(BTRIM(s.domaine_insertion), ''), '') <> ''
              OR COALESCE(NULLIF(BTRIM(s.domaine_insertion_autres), ''), '') <> ''
              OR COALESCE(NULLIF(BTRIM(s.type_insertion), ''), '') <> ''
              OR COALESCE(NULLIF(BTRIM(s.type_insertion_autres), ''), '') <> ''
          )
    """

    with transaction.atomic():
        _exec_rowcount(delete_sql, params)
        affected = _exec_rowcount(insert_sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.ent_insertion_dom", "affected_rows": affected},
        ],
    }


def _publish_fiere_participation(*, project_code: str, region_id: str | None) -> dict[str, Any]:
    filters: list[str] = ["s.project_code = %s"]
    params: list[Any] = [project_code]

    if region_id:
        filters.append("ac.id_region = %s")
        params.append(region_id)

    where_sql = " AND ".join(filters)

    stage_count = _count_scalar(
        f"""
        SELECT COUNT(*)
        FROM stage.fiere_participation_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        """,
        params,
    )

    sql = f"""
        INSERT INTO core.acteur_participation (
            raw_uuid,
            project_code,
            type_acteur,
            type_acteur_autres,
            est_entreprise_fiere,
            id_ent,
            nom_acteur,
            date_derniere_part,
            type_participation,
            type_participation_autres,
            statut_convention,
            intitule_dispositif,
            objet_participation,
            objet_participation_autres,
            nb_part_12m,
            frequence_particip,
            niveau_implication,
            satisfaction_globale,
            resultats_obtenus,
            contraintes_particip,
            obs_participation,
            id_commune,
            id_prefecture,
            id_region,
            localite,
            geom,
            raw_payload,
            import_source,
            import_batch,
            imported_at,
            is_active,
            record_source,
            updated_at
        )
        SELECT
            s.raw_uuid,
            s.project_code,
            s.type_acteur,
            s.type_acteur_autres,
            s.est_entreprise_fiere,
            s.id_ent,
            s.nom_acteur,
            s.date_derniere_part,
            s.type_participation,
            s.type_participation_autres,
            s.statut_convention,
            s.intitule_dispositif,
            s.objet_participation,
            s.objet_participation_autres,
            s.nb_part_12m,
            s.frequence_particip,
            s.niveau_implication,
            s.satisfaction_globale,
            s.resultats_obtenus,
            s.contraintes_particip,
            s.obs_participation,
            s.id_commune,
            ac.id_prefecture,
            ac.id_region,
            s.localite,
            s.geom,
            s.raw_payload,
            s.import_source,
            s.import_batch,
            s.imported_at,
            true,
            COALESCE(NULLIF(s.import_source, ''), 'admin_csv'),
            now()
        FROM stage.fiere_participation_raw s
        JOIN ref.admin_commune ac ON ac.id_commune = s.id_commune
        WHERE {where_sql}
          AND COALESCE(NULLIF(BTRIM(s.id_commune), ''), '') <> ''
        ON CONFLICT (raw_uuid) DO UPDATE SET
            project_code = EXCLUDED.project_code,
            type_acteur = EXCLUDED.type_acteur,
            type_acteur_autres = EXCLUDED.type_acteur_autres,
            est_entreprise_fiere = EXCLUDED.est_entreprise_fiere,
            id_ent = EXCLUDED.id_ent,
            nom_acteur = EXCLUDED.nom_acteur,
            date_derniere_part = EXCLUDED.date_derniere_part,
            type_participation = EXCLUDED.type_participation,
            type_participation_autres = EXCLUDED.type_participation_autres,
            statut_convention = EXCLUDED.statut_convention,
            intitule_dispositif = EXCLUDED.intitule_dispositif,
            objet_participation = EXCLUDED.objet_participation,
            objet_participation_autres = EXCLUDED.objet_participation_autres,
            nb_part_12m = EXCLUDED.nb_part_12m,
            frequence_particip = EXCLUDED.frequence_particip,
            niveau_implication = EXCLUDED.niveau_implication,
            satisfaction_globale = EXCLUDED.satisfaction_globale,
            resultats_obtenus = EXCLUDED.resultats_obtenus,
            contraintes_particip = EXCLUDED.contraintes_particip,
            obs_participation = EXCLUDED.obs_participation,
            id_commune = EXCLUDED.id_commune,
            id_prefecture = EXCLUDED.id_prefecture,
            id_region = EXCLUDED.id_region,
            localite = EXCLUDED.localite,
            geom = EXCLUDED.geom,
            raw_payload = EXCLUDED.raw_payload,
            import_source = EXCLUDED.import_source,
            import_batch = EXCLUDED.import_batch,
            imported_at = EXCLUDED.imported_at,
            is_active = true,
            record_source = EXCLUDED.record_source,
            updated_at = now()
    """

    with transaction.atomic():
        affected = _exec_rowcount(sql, params)

    return {
        "stage_count": stage_count,
        "core_tables": [
            {"table": "core.acteur_participation", "affected_rows": affected},
        ],
    }
