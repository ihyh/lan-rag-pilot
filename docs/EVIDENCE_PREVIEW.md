# Isolated evidence preview

Experimental opt-in factory `app.evidence_preview.create_preview_app(db_path, embed_query, index)`.
It is **not mounted by the normal app**, and is not a production deployment.

Supply a separate SQLite database containing the existing schema, synthetic preview users/sessions,
and explicitly authorized document copies. Never copy production credentials or sessions.
Bind a single server to `127.0.0.1`, on a non-production port. Do not expose this diagnostic tool
through a LAN, VPN, proxy, Funnel or public service. Load a local embedding model explicitly/offline;
the factory downloads nothing, writes no database data, and calls no generation model.

The browser requires a separate preview access code. `rag_evidence_preview` is HttpOnly/SameSite=Strict
and path-scoped to `/api/preview`; the production cookie is ignored. Requests require the same Origin;
Host is limited to loopback names. This is a local diagnostic tool, not a public login/rate-limit service.

1. Select document scope and one or more explicit purposes, then search. ACL is applied before the
   supplied index, then actual DB identities/text are bound by `prepare_evidence_request`.
   Existing vector/technical-keyword retrieval is used; experimental general lexical flags remain off.
2. Select the original ranked snippets, then render. A server-held, session-bound receipt lasts five
   minutes (up to 64 receipts); clients cannot supply their own digest/text/purpose. Authorization,
   source content/version and account status are checked again before JSON or buffered NDJSON.
3. Original text uses DOM `textContent`, never HTML/Markdown execution. Source references use explicit
   `source_id`, including sparse `[2]` when `[1]` is unselected. Query/scope/purpose changes invalidate
   the browser selection. Incomplete NDJSON is not displayed.

This deliberately shows **source excerpts, not generated answers**. Current-state/remedy requests
return explicit evidence gaps; mixed requests preserve excerpts and gaps separately. Retrieval rank,
user selection, an HTTP pass and literal text fidelity do not certify relevance, completeness,
conflict resolution, present device state or repair applicability. Semantic/onsite flags remain false.
Concurrent revocation across multiple SQL statements is not atomic; already transmitted bytes
cannot be withdrawn. See [caller contract](EVIDENCE_REQUEST.md).

Run `python tests/evidence_preview_check.py` for synthetic HTTP/ACL controls. Genuine embedding,
authorized corpus retrieval and browser behavior require a separate isolated acceptance record;
do not report these checks as business answer accuracy.
