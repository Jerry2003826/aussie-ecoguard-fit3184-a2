import { useEffect, useState } from 'react';
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Leaf, Images, Search, Heart, Bell, LogOut, Menu, X, ArrowUpRight } from 'lucide-react';
import { AuthProvider, useAuth } from './auth/AuthProvider';
import { AuthPage } from './auth/AuthPage';
import { Library } from './pages/Library';
import { MediaDetail } from './pages/MediaDetail';
import { SearchPage } from './pages/SearchPage';
import { PetSearch } from './pages/PetSearch';
import { Subscriptions } from './pages/Subscriptions';
import { Chat } from './components/Chat';
import { ErrorNotice, Spinner } from './components/ui';
import { ApiError } from './api/client';
import { config } from './config';
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) =>
        !(error instanceof ApiError && error.status > 0 && error.status < 500) && count < 1,
      refetchOnWindowFocus: true,
      staleTime: 15000,
    },
  },
});
function Shell() {
  const auth = useAuth();
  const [menu, setMenu] = useState(false);
  const [error, setError] = useState<unknown>();
  const location = useLocation();
  useEffect(() => {
    setMenu(false);
    document.getElementById('main-content')?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }, [location.pathname]);
  useEffect(() => {
    queryClient.clear();
  }, [auth.user?.id]);
  async function logout() {
    setError(undefined);
    try {
      await auth.logout();
      queryClient.clear();
    } catch (e) {
      setError(e);
    }
  }
  if (auth.loading)
    return (
      <div className="app-loading">
        <Spinner text="Opening EcoGuard…" />
      </div>
    );
  if (!auth.user) return <AuthPage />;
  return (
    <div className="app-shell" key={auth.user.id}>
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <div className="mobile-header">
        <Link className="brand" to="/media">
          <span className="brand-mark">
            <Leaf size={20} />
          </span>
          <span>
            Aussie <b>EcoGuard</b>
          </span>
        </Link>
        <button
          className="icon-button"
          aria-label={menu ? 'Close navigation' : 'Open navigation'}
          aria-expanded={menu}
          onClick={() => setMenu(!menu)}
        >
          {menu ? <X /> : <Menu />}
        </button>
      </div>
      {menu && <div className="sidebar-overlay" onClick={() => setMenu(false)} />}
      <aside className={`sidebar ${menu ? 'open' : ''}`}>
        <Link className="brand" to="/media">
          <span className="brand-mark">
            <Leaf size={25} />
          </span>
          <span>
            Aussie
            <br />
            <b>EcoGuard</b>
          </span>
        </Link>
        <p className="nav-caption">YOUR WORKSPACE</p>
        <nav aria-label="Main navigation">
          {[
            { path: '/media', icon: Images, title: 'My media' },
            { path: '/search', icon: Search, title: 'Explore sightings' },
            { path: '/pets', icon: Heart, title: 'Find a pet' },
            { path: '/subscriptions', icon: Bell, title: 'Email alerts' },
          ].map(({ path, icon: Icon, title }) => (
            <NavLink key={path} to={path}>
              <Icon size={19} />
              <span>{title}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-note">
          <div className="tiny-leaf">
            <Leaf size={20} />
          </div>
          <h3>
            A little care
            <br />
            goes a long way.
          </h3>
          <p>Your observations help connect wildlife and community.</p>
          <Link to="/pets">
            Look for a pet <ArrowUpRight size={14} />
          </Link>
        </div>
        <div className="account">
          <div className="avatar">{auth.user.name.slice(0, 1)}</div>
          <div>
            <strong>{auth.user.name}</strong>
            <small>{auth.user.local ? 'Local test identity' : 'Community member'}</small>
          </div>
          <button
            className="icon-button"
            aria-label="Sign out"
            title="Sign out"
            onClick={() => void logout()}
          >
            <LogOut size={18} />
          </button>
        </div>
      </aside>
      <div className="workspace">
        <div className="topbar">
          <span>Wildlife & community, connected.</span>
          <span className="topbar-label">
            <Leaf size={14} /> AUSTRALIAN SIGHTINGS
          </span>
        </div>
        {config.devMode && (
          <div className="dev-banner" role="status">
            <strong>LOCAL DEVELOPMENT</strong> Local API adapter · {auth.user.name} · Not cloud
            acceptance. AI, Cognito, email and Lex require real cloud verification.
          </div>
        )}
        <main id="main-content" tabIndex={-1} className="main-content">
          <ErrorNotice error={error} />
          <Routes>
            <Route path="/" element={<Navigate to="/media" replace />} />
            <Route path="/media" element={<Library />} />
            <Route path="/media/:id" element={<MediaDetail />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/pets" element={<PetSearch />} />
            <Route path="/subscriptions" element={<Subscriptions />} />
            <Route path="*" element={<Navigate to="/media" replace />} />
          </Routes>
          <footer className="page-footer">
            <Leaf size={13} /> Aussie EcoGuard{' '}
            <span>Thoughtful observations. Meaningful connections.</span>
          </footer>
        </main>
      </div>
      <Chat key={auth.user.id} />
    </div>
  );
}
export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Shell />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
