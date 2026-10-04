from habitat.catalog.store import MemoryCatalog
from habitat.recipe_inputs import FAMILIES, SITE_FEATURES, WATER, HabitatRecipeCatalog, to_recipe_dataset
from test_recipe_inputs import habitat_dataset, query


def test_water_variables_and_site_features_map_to_their_recipe_families():
    water = to_recipe_dataset(habitat_dataset(
        "jrc_gsw_monthly--gsw--ease2-global-1km", "cell_observations",
        ["surface_water_fraction", "distance_to_surface_water_m"],
    ))
    derived = to_recipe_dataset(habitat_dataset(
        "water_derived--water-derived--ease2-global-1km", "cell_observations",
        ["distance_to_water_m", "water_point_density"],
    ))
    features = to_recipe_dataset(habitat_dataset("wpdx--wpdx-plus--ease2-global-1km", SITE_FEATURES, ["water_point"]))

    assert water.family == WATER and derived.family == WATER
    assert {c.name for c in water.columns} >= {"cell_id", "interval_start", "interval_end", "variable", "value", "unit"}
    assert features.family == SITE_FEATURES
    roles = {c.role: c.name for c in features.columns}
    assert roles["geometry"] == "geometry" and roles["available_at"] == "available_at"
    assert {"feature_id", "feature_class", "permanence", "valid_until"} <= {c.name for c in features.columns}


def test_a_mixed_water_and_vegetation_dataset_has_no_recipe_family():
    mixed = habitat_dataset("x--y--ease2-global-1km", "cell_observations", ["ndvi", "surface_water_fraction"])

    assert to_recipe_dataset(mixed) is None


def test_metadata_search_finds_the_water_families_and_binds_their_views():
    memory = MemoryCatalog()
    memory.register_dataset(habitat_dataset("water_derived--water-derived--ease2-global-1km", "cell_observations",
                                            ["distance_to_water_m"]))
    memory.register_dataset(habitat_dataset("osm_overpass--osm-water--ease2-global-1km", SITE_FEATURES, ["river"]))
    catalog = HabitatRecipeCatalog(memory, allowed_scopes={"public"})

    water = catalog.search_metadata({"family": WATER}, query=query(), limit=10)
    features = catalog.search_metadata({"family": SITE_FEATURES}, query=query(), limit=10)

    [distances], [rivers] = water.datasets, features.datasets
    assert catalog.bindings[distances.key].table == "recipe_water_observations"
    assert catalog.bindings[rivers.key].table == "recipe_site_features"
    assert catalog.bindings[rivers.key].native_geometry_columns == {"geometry"}


def test_every_descriptor_column_is_a_column_of_its_view(database):
    for family in (FAMILIES[WATER], FAMILIES[SITE_FEATURES]):
        with database.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {family.view} LIMIT 0")
            view_columns = {column.name for column in cursor.description}

        assert {column.name for column in family.columns} <= view_columns, family.view


def test_the_reader_role_can_read_the_new_views(database):
    for view in ("site_features", "recipe_site_features", "recipe_water_observations"):
        granted = database.execute(
            "SELECT has_table_privilege('habitat_reader', %s, 'SELECT')", (view,)
        ).fetchone()
        assert granted == (True,), view
