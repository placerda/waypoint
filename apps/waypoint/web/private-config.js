export function validatePrivateConfiguration(env) {
  if (env.NODE_ENV !== "production") return;
  for (const key of [
    "API_ENDPOINT_HTTP",
    "WAYPOINT_MSAL_TENANT_ID",
    "WAYPOINT_MSAL_CLIENT_ID",
    "WAYPOINT_MSAL_API_SCOPE",
    "WAYPOINT_MSAL_REDIRECT_URI",
  ]) {
    if (!env[key]?.trim()) throw new Error(`${key} is required in production`);
  }
  if (!["true", "1", "yes", "on"].includes(env.WAYPOINT_MSAL_ENABLED?.toLowerCase())) {
    throw new Error("Native delegated-user authentication is required in production");
  }
  for (const key of ["API_ENDPOINT_HTTP", "WAYPOINT_MSAL_REDIRECT_URI"]) {
    const url = new URL(env[key]);
    if (url.protocol !== "https:" || url.username || url.password) {
      throw new Error(`${key} must be an HTTPS URL without embedded credentials`);
    }
  }
}
