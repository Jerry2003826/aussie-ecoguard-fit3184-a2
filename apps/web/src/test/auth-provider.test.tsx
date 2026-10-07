import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fetchUserAttributes, getCurrentUser, signOut } from 'aws-amplify/auth';
import { AuthProvider, useAuth } from '../auth/AuthProvider';

vi.mock('aws-amplify', () => ({ Amplify: { configure: vi.fn() } }));
vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: vi.fn(),
  fetchUserAttributes: vi.fn(),
  getCurrentUser: vi.fn(),
  signOut: vi.fn(),
}));
vi.mock('../config', () => ({
  authConfigured: true,
  config: { userPoolId: 'test-pool', clientId: 'test-client', devMode: false },
}));
vi.mock('../api/client', () => ({ setCredentials: vi.fn() }));

function AuthState() {
  const { user, loading } = useAuth();
  return <span>{loading ? 'Loading session' : user ? 'Authenticated' : 'Signed out'}</span>;
}

beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);

it('clears a stale SDK session when its account can no longer be read', async () => {
  vi.mocked(getCurrentUser).mockResolvedValue({ userId: 'deleted-user', username: 'test-user' });
  vi.mocked(fetchUserAttributes).mockRejectedValue(new Error('The user no longer exists.'));
  vi.mocked(signOut).mockResolvedValue(undefined);
  render(
    <AuthProvider>
      <AuthState />
    </AuthProvider>,
  );
  await screen.findByText('Signed out');
  expect(signOut).toHaveBeenCalledOnce();
});

it('keeps a valid SDK session when the account attributes load', async () => {
  vi.mocked(getCurrentUser).mockResolvedValue({ userId: 'active-user', username: 'test-user' });
  vi.mocked(fetchUserAttributes).mockResolvedValue({
    email: 'test@example.invalid',
    given_name: 'Test',
  });
  render(
    <AuthProvider>
      <AuthState />
    </AuthProvider>,
  );
  await waitFor(() => expect(screen.getByText('Authenticated')).toBeVisible());
  expect(signOut).not.toHaveBeenCalled();
});
