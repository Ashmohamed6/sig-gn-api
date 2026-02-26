from django.db import migrations


FORWARD_SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'ref'
          AND table_name = 'hydrographie'
          AND column_name = 'geom'
    ) THEN
        ALTER TABLE ref.hydrographie
        ALTER COLUMN geom TYPE geometry(MultiLineString, 4326)
        USING (
            CASE
                WHEN geom IS NULL THEN NULL
                WHEN ST_GeometryType(geom) IN ('ST_LineString', 'ST_MultiLineString')
                    THEN ST_Multi(ST_Force2D(geom))
                WHEN ST_GeometryType(geom) = 'ST_GeometryCollection'
                    THEN ST_Multi(ST_CollectionExtract(ST_Force2D(geom), 2))
                ELSE NULL
            END
        );

        CREATE INDEX IF NOT EXISTS sidx_hydrographie_geom
            ON ref.hydrographie
            USING GIST (geom);
    END IF;
END $$;
"""


REVERSE_SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'ref'
          AND table_name = 'hydrographie'
          AND column_name = 'geom'
    ) THEN
        ALTER TABLE ref.hydrographie
        ALTER COLUMN geom TYPE geometry(Point, 4326)
        USING (
            CASE
                WHEN geom IS NULL THEN NULL
                ELSE ST_PointOnSurface(geom)
            END
        );

        CREATE INDEX IF NOT EXISTS sidx_hydrographie_geom
            ON ref.hydrographie
            USING GIST (geom);
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
