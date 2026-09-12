// Explicit local data migration, never run at application startup.
// Run with mongosh --file; dry-run unless APPLY_LEGACY_PBLC_DELETE=yes.
const legacyTokens = [
  "0xded7f4992d98ef31453dcebbb8c2a6b50d0284b3",
  "0xe75013d333bebb90b321dd658440c10b5a0face8",
];
const name = db.getName();
if (!["aegis_live_payment", "pbl_audit"].includes(name)) {
  throw new Error("Only the two inspected PBL databases are in scope");
}
const apply = process.env.APPLY_LEGACY_PBLC_DELETE === "yes";
const ids = db.purchaseEvents.distinct("purchaseId", {
  type: "DECIDED", "payload.token.address": { $in: legacyTokens },
});
// These two pre-event failed PBLC requests were inspected in the legacy DB.
if (name === "pbl_audit") ids.push(
  "c96be5f1-32b8-486e-aea7-da0e10ff99f2", "6bebee43-1284-40c6-a0fb-194aa4ddd78b",
);
const byPurchase = { purchaseId: { $in: ids } };
const targets = [
  "purchaseEvents", "evidenceHeads", "sensitivePayloads", "immutableDocuments",
  "paymentIntents", "sellerExecutions", "confirmedOutflows", "reputationOutbox",
].map(collection => ({ collection, filter: byPurchase }));
targets.push({ collection: "walletPolicies", filter: { token: { $in: legacyTokens } } });
print(JSON.stringify({ database: name, apply, purchases: ids.length,
  counts: targets.map(({ collection, filter }) => ({ collection,
    count: db.getCollection(collection).countDocuments(filter) })) }));
if (apply) {
  for (const { collection, filter } of targets) {
    print(JSON.stringify({ collection, deleted: db.getCollection(collection).deleteMany(filter).deletedCount }));
  }
}
