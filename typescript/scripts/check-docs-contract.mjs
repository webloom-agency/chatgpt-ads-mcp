const pages = [
  {
    name: "insights",
    url: "https://developers.openai.com/ads/api-reference/insights",
    markers: ["time_ranges", "unix_range", "cpc"],
  },
  {
    name: "conversions",
    url: "https://developers.openai.com/ads/conversions-api",
    markers: ["validate_only", "obref", "app_installed", "app_opened"],
  },
  {
    name: "conversion optimization",
    url: "https://developers.openai.com/ads/conversion-optimized-campaigns",
    markers: ["bidding_type", "conversion_event_setting_ids", "product feed"],
  },
  {
    name: "account setup",
    url: "https://developers.openai.com/ads/api-partner-setup",
    markers: ["currency_code", "review.status", "missing_favicon"],
  },
  {
    name: "ad creative",
    url: "https://developers.openai.com/ads/api-reference/ads",
    markers: ["chat_card", "file_id", "target_url"],
  },
  {
    name: "bulk API",
    url: "https://developers.openai.com/ads/bulk-api",
    markers: ["limited preview", "validate_only", "1000"],
  },
];

const failures = [];
for (const page of pages) {
  const response = await fetch(page.url, {
    headers: { "User-Agent": "openai-ads-mcp-doc-contract-check" },
  });
  if (!response.ok) {
    failures.push(`${page.name}: HTTP ${response.status}`);
    continue;
  }
  const body = (await response.text()).toLowerCase();
  const missing = page.markers.filter((marker) => !body.includes(marker.toLowerCase()));
  if (missing.length) failures.push(`${page.name}: missing ${missing.join(", ")}`);
}

if (failures.length) {
  throw new Error(
    `OpenAI Ads guide contract may have changed:\n${failures.map((failure) => `- ${failure}`).join("\n")}`,
  );
}

console.log(`OpenAI Ads guide contract markers still present across ${pages.length} pages.`);
