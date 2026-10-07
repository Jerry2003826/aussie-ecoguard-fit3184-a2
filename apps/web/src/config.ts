export const config = {
  apiBase: (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, ''),
  userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID || '',
  clientId: import.meta.env.VITE_COGNITO_CLIENT_ID || '',
  devMode:
    import.meta.env.VITE_DEV_MODE === 'true' &&
    ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname),
};
export const authConfigured = Boolean(config.userPoolId && config.clientId);
