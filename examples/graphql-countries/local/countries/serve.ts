// Serves upstream's GraphQL Yoga handler on port 4000; queries go to /graphql.
import { yoga } from "./src/graphql";

Bun.serve({ port: 4000, fetch: yoga.fetch });
console.log("countries listening on :4000");
