const AGENT_URL_KEY = "INVESTIGATION_AGENT_URL";

export function getAgentBaseUrl(
  environment: Readonly<Record<string, string | undefined>> = process.env,
): URL {
  const value = environment[AGENT_URL_KEY];
  if (!value) {
    throw new Error(`${AGENT_URL_KEY} is required`);
  }

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${AGENT_URL_KEY} must be an absolute HTTP(S) URL`);
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`${AGENT_URL_KEY} must use HTTP or HTTPS`);
  }
  url.pathname = url.pathname.replace(/\/$/, "");
  url.search = "";
  url.hash = "";
  return url;
}
