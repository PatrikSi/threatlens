## Summary

- 

## Validation

- [ ] Backend tests: `./backend/.venv/bin/pytest backend/tests -q`
- [ ] Frontend tests: `cd web && npm run test`
- [ ] Frontend lint/build when UI code changes: `cd web && npm run lint && npm run build`
- [ ] Docs, OpenAPI, and compliance artifacts updated when the public contract, dependencies, or shipped assets changed

## Security and Operations Notes

- [ ] No secrets, credentials, production data, or private hostnames are included
- [ ] New outbound network behavior is documented and covered by safe defaults
- [ ] New background work has bounded retries, idempotency, and operator-visible failure states
