from habitat.catalog.store import MemoryCatalog
from habitat.recipe_inputs import FAMILIES, POINT_EVENTS, SEARCH_FILTERS, HabitatRecipeCatalog, to_recipe_dataset
from test_recipe_inputs import habitat_dataset, query

FIRES = "firms_modis--firms-modis-sp--ease2-global-1km"


def test_a_point_events_dataset_maps_to_the_recipe_point_events_family():
    dataset = to_recipe_dataset(habitat_dataset(FIRES, POINT_EVENTS, ["active_fire"]))

    assert dataset.family == POINT_EVENTS
    roles = {column.role: column.name for column in dataset.columns if column.role}
    assert roles["event_time"] == "time_start"
    assert (roles["longitude"], roles["latitude"], roles["cell_id"]) == ("longitude", "latitude", "cell_id")
    assert roles["species"] == "species" and roles["available_at"] == "available_at"
    assert {column.name: column.unit for column in dataset.columns}["value"] is None
    assert "point_events" in SEARCH_FILTERS["family"]


def test_metadata_search_finds_events_and_binds_the_view():
    catalog = MemoryCatalog()
    catalog.register_dataset(habitat_dataset(FIRES, POINT_EVENTS, ["active_fire"]))
    recipe_catalog = HabitatRecipeCatalog(catalog, allowed_scopes={"public"})

    [dataset] = recipe_catalog.search_metadata({"family": POINT_EVENTS}, query=query(), limit=10).datasets

    assert recipe_catalog.bindings[dataset.key].table == "recipe_point_events"


def test_every_descriptor_column_is_a_column_of_the_view(database):
    columns = {
        name for (name,) in database.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'recipe_point_events'"
        )
    }

    assert {column.name for column in FAMILIES[POINT_EVENTS].columns} <= columns
