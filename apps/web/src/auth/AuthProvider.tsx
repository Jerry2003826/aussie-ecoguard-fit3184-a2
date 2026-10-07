import { createContext, useContext, useEffect, useLayoutEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Amplify } from 'aws-amplify';
import { fetchAuthSession, fetchUserAttributes, getCurrentUser, signOut } from 'aws-amplify/auth';
import { authConfigured, config } from '../config';
import { setCredentials } from '../api/client';

if (authConfigured)
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: config.userPoolId,
        userPoolClientId: config.clientId,
        loginWith: { email: true },
        signUpVerificationMethod: 'code',
      },
    },
  });
type User = { id: string; email: string; name: string; local: boolean };
type AuthValue = {
  user: User | null;
  loading: boolean;
  expired: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
  enterLocal: (id: string) => void;
};
const AuthContext = createContext<AuthValue | undefined>(undefined);
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [expired, setExpired] = useState(false);
  async function refresh() {
    if (!authConfigured) {
      setLoading(false);
      return;
    }
    try {
      const current = await getCurrentUser();
      const attributes = await fetchUserAttributes();
      setUser({
        id: current.userId,
        email: attributes.email || current.username,
        name: attributes.given_name || 'Explorer',
        local: false,
      });
      setExpired(false);
    } catch {
      // An invalid or deleted account must not leave a cached SDK session blocking sign-in.
      await signOut().catch(() => undefined);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }
  function enterLocal(id: string) {
    if (!config.devMode || !['alice', 'bob'].includes(id)) return;
    sessionStorage.setItem('ecoguard.localUser', id);
    setUser({
      id,
      email: `${id}@local.invalid`,
      name: id[0].toUpperCase() + id.slice(1),
      local: true,
    });
    setExpired(false);
  }
  useEffect(() => {
    const local = config.devMode && sessionStorage.getItem('ecoguard.localUser');
    if (local && ['alice', 'bob'].includes(local)) {
      enterLocal(local);
      setLoading(false);
    } else void refresh();
    const expiredListener = () => {
      setUser(null);
      setExpired(true);
      sessionStorage.removeItem('ecoguard.localUser');
    };
    window.addEventListener('ecoguard:unauthorized', expiredListener);
    return () => window.removeEventListener('ecoguard:unauthorized', expiredListener);
  }, []);
  useLayoutEffect(() => {
    setCredentials(async () => {
      if (user?.local && config.devMode) return { devUser: user.id };
      if (!user || !authConfigured) return {};
      try {
        const session = await fetchAuthSession();
        const token = session.tokens?.accessToken?.toString();
        if (!token) throw new Error('Missing access token');
        return { token };
      } catch {
        window.dispatchEvent(new Event('ecoguard:unauthorized'));
        throw new Error('Your session has expired. Please sign in again.');
      }
    });
  }, [user]);
  async function logout() {
    if (!user?.local && authConfigured) await signOut();
    sessionStorage.removeItem('ecoguard.localUser');
    setUser(null);
  }
  return (
    <AuthContext.Provider value={{ user, loading, expired, refresh, logout, enterLocal }}>
      {children}
    </AuthContext.Provider>
  );
}
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('AuthProvider is missing');
  return value;
}
