# Docker workspace

## Start

Install Docker with Docker Compose support.
Run all commands from the repository root.

```sh
docker compose up --build -d --wait
```

Open http://localhost:8080.
The first build downloads Python, Node, PostgreSQL, and the locked application dependencies.
Subsequent builds reuse unchanged layers.
The images support Apple Silicon and x86-64 hosts.

## Services

| Service | Purpose | Startup condition |
| --- | --- | --- |
| `db` | PostgreSQL 17 with PostGIS | None |
| `migrate` | Apply pending SQL migrations | Healthy database |
| `classifier` | GLiClass dataset classification on CPU | Cached or downloaded model weights |
| `app` | Serve the frontend, API, assistant, and pipeline | Healthy database, healthy classifier, and successful migrations |
| `frontend` | Vite development server with hot reload; development setup only | Healthy app |

Fetch, Normalize, Recipe, and Analysis execute inside the app container.
The frontend uses relative `/api` URLs on the same origin.
The database is accessible only within the Compose network.
The app port binds to the local host.

The root `compose.yaml` provides the complete workspace.
The separate `web/compose.yaml` provides optional GeoServer services for older map adapters.

## Configuration

Compose reads the root `.env` file and shell environment.
Use `.env.example` as a reference.

| Variable | Default | Purpose |
| --- | --- | --- |
| `HABITAT_PORT` | `8080` | Host port for the app |
| `HABITAT_FRONTEND_PORT` | `5173` | Host port for the Docker development frontend |
| `HABITAT_POSTGRES_PASSWORD` | `habitat-local` | Password for the container database |
| `OPENAI_API_KEY` | Empty | Enable the assistant and model-based planning |
| `MOVEBANK_USERNAME` | Empty | Authenticate Movebank study requests |
| `MOVEBANK_PASSWORD` | Empty | Authenticate Movebank study requests |
| `HABITAT_CLASSIFIER_THRESHOLD` | `0.7` | Minimum score for model tags |
| `GLICLASS_THREADS` | `2` | CPU threads for classification |

Choose the database password before the first start.
Changing the environment value does not change an existing database password.
Compose supplies its internal database connection directly.
The root `HABITAT_DATABASE_URL` applies to standalone Python commands.
The Docker image excludes `.env`, local data, and credentials.

## Local classification

The classifier runs [`knowledgator/gliclass-small-v1.0`](https://huggingface.co/knowledgator/gliclass-small-v1.0) on CPU.
Both the [GLiClass code](https://github.com/Knowledgator/GLiClass) and these model weights use the Apache-2.0 license.
The model revision is pinned in Compose.
The first start downloads the weights into the `classifier-models` volume.
Startup waits for model loading and a successful inference check.
The initial download can take several minutes.
Later starts reuse the cached model.

The classifier adds labels from `contracts/tag_vocabulary.json` when the pipeline publishes new datasets.
The checkpoint uses single-label classification.
The backend requests each tag category separately and accepts at most one label per category.
Each category includes an option for unspecified metadata.
Scores below the configured threshold produce no tag.
Each model tag records its score, model identifier, and revision.
Deterministic tags retain priority.
Classification scores indicate model confidence, not measured ecological evidence.
An unavailable classifier leaves deterministic tagging active and records a warning in the app logs.
The classifier retains the dataset description instead of generating a summary.
Chatbot answers and structured analysis planning use the configured OpenAI models.

The internal service accepts `POST http://classifier:8001/classify` with `text`, `labels`, and an optional `threshold`.
The service accepts at most 25 labels and returns the highest-scoring label when its score meets the threshold.
The response includes model metadata and a label-score pair.
Dataset metadata stays within the Compose network during classification.

Test custom labels from the app container:

```sh
docker compose exec app python -c 'import httpx; print(httpx.post("http://classifier:8001/classify", json={"text": "GPS collar observations of seasonal deer migration", "labels": ["animal movement", "rainfall", "vegetation"], "threshold": 0.5}, timeout=60).json())'
```

For a standalone backend, set `HABITAT_CLASSIFIER_URL` to a reachable classifier address.

After a configuration change, run:

```sh
docker compose up -d --wait
```

## Docker development with hot reload

Start the development stack:

```sh
docker compose -f compose.yaml -f compose.dev.yaml up --build -d --wait
```

Open http://localhost:5173.
Vite runs inside Docker and forwards `/api` requests to `http://app:8000`.
Frontend changes appear through Vite hot reload without an image rebuild.
The app restarts automatically after changes in `src/` or `contracts/`.
Both watchers use polling to detect changes on Docker Desktop.
Backend restarts interrupt active requests.
Frontend dependencies remain inside the frontend image.

Use both Compose files for development commands:

```sh
docker compose -f compose.yaml -f compose.dev.yaml logs --tail=100 app frontend
docker compose -f compose.yaml -f compose.dev.yaml down
```

After dependency changes, run the development startup command again.
Add SQL migrations, then apply pending migrations:

```sh
docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate
```

To return to the built frontend, run:

```sh
docker compose -f compose.yaml -f compose.dev.yaml down
docker compose up --build -d --wait
```

Open http://localhost:8080 for the built frontend.

### Frontend development outside Docker

Start the backend stack, then run Vite locally:

```sh
docker compose up --build -d --wait
pnpm --dir web install
pnpm --dir web run dev
```

Open http://localhost:5173.
Local Vite forwards `/api` to http://127.0.0.1:8080.

For another backend port, set `HABITAT_API_TARGET` in `web/.env.local`:

```dotenv
HABITAT_API_TARGET=http://127.0.0.1:8081
```

## Retrieve data

The new database starts with an empty catalog.
The assistant offers retrieval when relevant data is absent.
Confirm the source, region, and dates when the assistant asks.
Published datasets appear in the workspace automatically.

Run a known-source pipeline request directly:

```sh
docker compose exec app dora chirps \
  --bbox 36,-2,36.1,-1.9 \
  --start 2024-01-01 --end 2024-01-02
```

This command retrieves real CHIRPS data and publishes normalized rainfall observations.
Select **Refresh** in the workspace after a command-line retrieval.

The new volumes do not contain data from an existing standalone database or the host `data/` directory.

## Storage and restarts

| Volume | Container path | Contents |
| --- | --- | --- |
| `database` | `/var/lib/postgresql/data` | Catalog, observations, and migration records |
| `artifacts` | `/app/data` | Temporary raw downloads, Recipe artifacts, and Analysis outputs |
| `classifier-models` | `/models` | Pinned GLiClass weights and tokenizer |

Stop the services and retain data:

```sh
docker compose down
```

Start the services again:

```sh
docker compose up -d --wait
```

## Migrations

The migration service applies `migrations/*.sql` in filename order.
The service records each filename and checksum in `habitat_schema_migrations`.
Each migration executes in a transaction.
A database lock prevents concurrent migration runs.
Startup skips recorded migrations and rejects changed migration files.
Add a new numbered SQL file for each schema change.

Inspect migration results:

```sh
docker compose logs migrate
docker compose exec db psql -U habitat -d habitat \
  -c 'SELECT filename, applied_at FROM habitat_schema_migrations ORDER BY filename;'
```

The service expects a fresh database or a database that already contains its migration records.

## Service checks

```sh
docker compose ps -a
docker compose logs --tail=100 app migrate db classifier
curl --fail http://localhost:8080/api/health
curl --fail http://localhost:8080/api/catalog
```

The health endpoint checks database connectivity and the catalog relation.
The app becomes healthy only when that check succeeds.
Missing model credentials disable the assistant but allow catalog and map access.

Run migration integration tests against temporary databases inside the stack:

```sh
docker compose run --rm --no-deps \
  -e HABITAT_MIGRATION_TESTS=1 \
  -v "$PWD/tests:/checks:ro" \
  migrate python -m unittest discover -s /checks -p test_migrate.py
```

The tests create isolated databases and remove those databases after each test.

Run classification checks against the loaded model:

```sh
docker compose run --rm --no-deps \
  -e HABITAT_CLASSIFIER_TEST_URL=http://classifier:8001 \
  -v "$PWD/tests:/checks:ro" \
  app python -m unittest discover -s /checks -p test_classifier_service.py
```
