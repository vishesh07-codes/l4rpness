# Real-world F5 scan — live production FastAPI services

## What F5 is (remote perspective)

F5: FastAPI inspects `Annotated` metadata only when `Annotated` is the outermost
type. `Annotated[T, Depends(guard)] | None` becomes `Optional[Annotated[...]]`,
the guard is dropped, and the parameter degrades to a normal request parameter
(body for models, query for scalars). The endpoint keeps looking protected:
a plain request returns the app's own 401, but supplying the parameter forges
identity.

## Remote detection method (validated)

Two independent checks, both non-destructive:

1. **Schema check** — fetch the public `/openapi.json` and look for the
   structural fingerprint:
   - a GET operation carrying a JSON `requestBody` (normal FastAPI apps never
     generate this; a dropped model dependency on a GET does),
   - model-typed query/header/cookie parameters,
   - identity-shaped bodies on protected paths.

2. **Empty-body probe** — for sampled static GET routes, compare
   `GET path` vs `GET path` with `Content-Type: application/json` and body `{}`.
   The F5 spelling turns the response into a body-validation error
   (`422 {"loc":["body",...]}`); normal FastAPI ignores bodies on GET.

Detector validated against a live reference app on FastAPI 0.142.2:

```
GET /me/orders  (Annotated[User, Depends(guard)] | None)  no-body=401  empty-body=422 {"loc":["body","username"]}
GET /me/safe    (Annotated[User | None, Depends(guard)])  no-body=401  empty-body=401
```

So the probe flags exactly the vulnerable spelling and nothing else.
No identity data was ever sent; the only body used was `{}` on read-only GETs.

## Targets (live FastAPI servers found in the wild)

Discovery: urlscan.io hosts whose responses carry `Server: uvicorn`
(the FastAPI/ASGI default), deduplicated to 81 origins, plus a curated set of
production FastAPI products. 56 origins served a public OpenAPI contract;
52 had analyzable paths and were probed.

Notable production services in the set:

| Service | note |
|---|---|
| api.supermemory.ai | AI memory API, FastAPI |
| api.trychroma.com | Chroma Cloud API, FastAPI |
| app.all-hands.dev | OpenHands Cloud API (186 paths) |
| demo.mealie.io | Mealie public demo (182 paths) |
| infer.roboflow.com | Roboflow Inference server |
| api.deepinfra.com | DeepInfra inference API (140 paths) |
| langserve-launch-example-…run.app | official LangServe launch example |
| lokalos.pl | production municipal portal API |
| containercrm.online | production CRM API |
| nexora-cloud.org | production cloud panel API (196 paths) |
| healthcheck.controlartcloud.com.br | production health API |
| devin-api.data-migration.sea12.com | production migration API |
| + 40 self-hosted deployments (dashboards, SaaS panels, regional services) | |

Known production FastAPI products whose schema is not public (FastAPI confirmed
from their open-source code): Polar (payments), Prefect Cloud (orchestration),
Dify Cloud (LLM platform), Chroma, vLLM/SGLang deployments, Airflow 3 API,
Netflix Dispatch, Onyx, Open WebUI, Chainlit, LiteLLM.

## Results — live production servers

- **Schema check: 0/56 hosts** expose a GET with a JSON body. No structural F5
  fingerprint in any public contract (52 with paths, ~4200 operations total).
- **Empty-body probe: 0/52 hosts** show the F5 signature. The only 3 flags were
  Google Cloud Run's ingress rejecting GET-with-body with an HTML `400`
  (LangServe demo `/input_schema`, `/output_schema`, `/config_schema`) — not a
  FastAPI body parse, not F5. One other flag was a required *query* parameter
  (`sync_token`) on controlartcloud, again not a body.
- No identity payload, no state change, no data access was performed at any
  point. A positive signal would have been reported as the structural evidence
  (401→422 transition), leaving the forged-identity step as the mechanical
  consequence already proven in the F5 PoC.

## Limitations (what this cannot see)

- Only the **GET + model-typed dependency** variant is remotely detectable
  this way. For POST endpoints a dropped model dependency merges into the body
  and is indistinguishable from a normal parameter; for scalar dependencies the
  dropped parameter becomes a query parameter named after the argument. Those
  variants require authenticated, in-scope testing to confirm.
- Coverage is a sample of `uvicorn`-exposed hosts indexed by urlscan plus
  products with public schemas; servers hiding the `Server` header or disabling
  `/openapi.json` are invisible to this pass.
- Complementary code scan (15 production repos: vLLM, LiteLLM, Prefect, Airflow,
  Dify, Onyx, OpenHands, Open WebUI, Mealie, Chroma, SGLang, Roboflow Inference,
  Chainlit, Polar, Netflix Dispatch): the union-wrapped `Depends`/`Security`
  spelling does not appear at endpoint level in any of them. Polar's union
  hits are service-layer calls, alias chains whose default `Query()` overrides
  the location, and two aliases on a service class app state that FastAPI never
  analyzes.

## Conclusion so far

No live production FastAPI server in this sample shows the F5 fingerprint. The
pattern is real and silently breaks the declared guard (proven against the
framework), but it requires the app to spell the dependency as an outer union —
a spelling that is rare in endpoint code, absent from the 15 scanned production
codebases, and not visible in 52 live public contracts. A production impact
claim therefore rests on the code pattern, not on observed live instances;
high-value targets to check with operator-authorized testing are the large
open-source FastAPI products listed above.
