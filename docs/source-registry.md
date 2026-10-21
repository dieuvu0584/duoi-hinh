# Source registry policy

Register sources explicitly. For each source record:
- name, publisher, source type (official, news outlet, wire, science/technology publication, RSS/API, user-submitted), canonical domain;
- permitted access method and applicable terms/licensing reviewed;
- reliability notes, supported topics, polling schedule, enabled state;
- last success/error and rate-limit policy;
- whether it is primary evidence or secondary reporting.

## Candidate source classes
- Vietnamese reputable news outlets.
- International wire services and established news organizations.
- Official government, public institution and emergency-agency releases.
- Scientific journals and established technology publications.
- RSS/API sources where use is permitted.
- User-submitted topics, clearly marked unverified until checked.
- Social trend data only through permitted, documented methods; never bypass access controls.

## Provenance rules
- Preserve canonical source URL and retrieval time.
- Keep article publication timestamp separate from event date.
- Do not count reposts of the same wire story as independent corroboration.
- Store only excerpts/content permitted by source terms; prefer metadata and links where unsure.
- For sensitive factual claims, require authoritative primary evidence or multiple independent reputable sources and human review.
- Never fabricate missing timestamps, sources or evidence.
