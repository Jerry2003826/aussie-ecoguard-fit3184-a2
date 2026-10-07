import { useState } from 'react';
import type { FormEvent } from 'react';
import {
  signIn,
  signUp,
  confirmSignUp,
  resendSignUpCode,
  confirmSignIn,
  resetPassword,
  confirmResetPassword,
} from 'aws-amplify/auth';
import { Leaf, ArrowRight, ShieldCheck, PawPrint, Image } from 'lucide-react';
import { useAuth } from './AuthProvider';
import { authConfigured, config } from '../config';
import { ErrorNotice, Notice } from '../components/ui';

type Mode = 'signin' | 'signup' | 'verify' | 'newPassword' | 'otp' | 'forgot' | 'reset';
const titles: Record<Mode, string> = {
  signin: 'Welcome back.',
  signup: 'A little observation. A big difference.',
  verify: 'Check your inbox.',
  newPassword: 'Make this account yours.',
  otp: 'One more step.',
  forgot: 'Let’s get you back in.',
  reset: 'Choose a new password.',
};
export function AuthPage() {
  const auth = useAuth();
  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [first, setFirst] = useState('');
  const [last, setLast] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState('');
  function changeMode(next: Mode) {
    setMode(next);
    setError(undefined);
    setMessage('');
    setPassword('');
    setCode('');
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    setMessage('');
    try {
      if (mode === 'signup') {
        const result = await signUp({
          username: email.trim(),
          password,
          options: {
            userAttributes: {
              email: email.trim(),
              given_name: first.trim(),
              family_name: last.trim(),
            },
          },
        });
        setPassword('');
        if (result.nextStep.signUpStep === 'CONFIRM_SIGN_UP') {
          setMode('verify');
          setMessage('We sent a verification code to your email.');
        } else {
          setMode('signin');
          setMessage('Your account is ready. Sign in to continue.');
        }
      } else if (mode === 'verify') {
        await confirmSignUp({ username: email.trim(), confirmationCode: code.trim() });
        setMode('signin');
        setCode('');
        setMessage('Email verified. You can now sign in.');
      } else if (mode === 'forgot') {
        const result = await resetPassword({ username: email.trim() });
        if (result.nextStep.resetPasswordStep === 'CONFIRM_RESET_PASSWORD_WITH_CODE') {
          setMode('reset');
          setMessage('If this account can receive a reset code, check your inbox.');
        } else {
          setMode('signin');
          setMessage('Password reset complete. Sign in to continue.');
        }
      } else if (mode === 'reset') {
        await confirmResetPassword({
          username: email.trim(),
          confirmationCode: code.trim(),
          newPassword: password,
        });
        setMode('signin');
        setPassword('');
        setCode('');
        setMessage('Password updated. Sign in with your new password.');
      } else {
        const result =
          mode === 'signin'
            ? await signIn({ username: email.trim(), password })
            : await confirmSignIn({
                challengeResponse: mode === 'newPassword' ? password : code.trim(),
                ...(mode === 'newPassword' && first && last
                  ? {
                      options: {
                        userAttributes: { given_name: first.trim(), family_name: last.trim() },
                      },
                    }
                  : {}),
              });
        if (result.isSignedIn) {
          setPassword('');
          await auth.refresh();
        } else {
          const step = result.nextStep.signInStep;
          setPassword('');
          if (step === 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED') {
            setMode('newPassword');
            setMessage('Set a permanent password to replace your temporary password.');
          } else if (step === 'CONFIRM_SIGN_UP') {
            setMode('verify');
            setMessage('Verify your email before signing in.');
          } else if (step === 'RESET_PASSWORD') {
            setMode('forgot');
          } else if (
            [
              'CONFIRM_SIGN_IN_WITH_EMAIL_CODE',
              'CONFIRM_SIGN_IN_WITH_SMS_CODE',
              'CONFIRM_SIGN_IN_WITH_TOTP_CODE',
            ].includes(step)
          ) {
            setMode('otp');
            setMessage('Enter the verification code from your configured sign-in method.');
          } else {
            throw new Error(
              `This account requires an additional sign-in method (${step}). Contact the project administrator.`,
            );
          }
        }
      }
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function resend() {
    setBusy(true);
    setError(undefined);
    try {
      await resendSignUpCode({ username: email.trim() });
      setMessage('A fresh verification code is on its way.');
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  const showPassword = ['signin', 'signup', 'newPassword', 'reset'].includes(mode);
  return (
    <div className="auth-layout">
      <section className="auth-story">
        <a className="brand auth-brand" href="/">
          <span className="brand-mark">
            <Leaf size={24} />
          </span>
          <span>
            Aussie <b>EcoGuard</b>
          </span>
        </a>
        <div className="story-content">
          <span className="eyebrow light">SMALL SIGHTINGS. SHARED PURPOSE.</span>
          <h1>
            Every sighting
            <br />
            has a story.
          </h1>
          <p>
            Keep an eye on our wildlife.
            <br />
            Help a familiar face find its way home.
          </p>
          <div className="story-art" aria-hidden="true">
            <div className="art-ring ring-one" />
            <div className="art-ring ring-two" />
            <Leaf className="art-leaf" />
            <PawPrint className="art-paw" />
            <div className="art-label">
              <Image size={18} /> Your observations, connected
            </div>
          </div>
        </div>
        <footer>
          <ShieldCheck size={18} /> Private by default. Shared when you choose.
        </footer>
      </section>
      <main className="auth-panel">
        <div className="auth-form-wrap">
          <span className="eyebrow">YOUR FIELD NOTEBOOK, ONLINE</span>
          <h2>{titles[mode]}</h2>
          <p className="muted">
            {mode === 'signin'
              ? 'Sign in to your animal sightings and community.'
              : mode === 'signup'
                ? 'Create an account to save sightings and search for pets.'
                : 'Follow the steps below to securely access your account.'}
          </p>
          {auth.expired && <Notice>Your session expired. Please sign in again.</Notice>}
          {!authConfigured && (
            <div className="notice warning">
              <ShieldCheck size={20} />
              <div>
                <strong>Cloud sign-in is not configured yet.</strong>
                <p>
                  Cognito user pool and app client settings are required. No account or cloud data
                  will be created here.
                </p>
              </div>
            </div>
          )}
          <ErrorNotice error={error} />
          {message && <Notice>{message}</Notice>}
          <form onSubmit={submit} className="form-stack">
            <label>
              Email address
              <input
                autoComplete="username"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={busy}
              />
            </label>
            {['signup', 'newPassword'].includes(mode) && (
              <div className="form-grid">
                <label>
                  First name
                  <input
                    autoComplete="given-name"
                    required
                    value={first}
                    onChange={(e) => setFirst(e.target.value)}
                  />
                </label>
                <label>
                  Last name
                  <input
                    autoComplete="family-name"
                    required
                    value={last}
                    onChange={(e) => setLast(e.target.value)}
                  />
                </label>
              </div>
            )}
            {showPassword && (
              <label>
                {mode === 'newPassword' || mode === 'reset' ? 'New password' : 'Password'}
                <input
                  type="password"
                  required
                  minLength={mode === 'signin' ? undefined : 8}
                  autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={busy}
                />
                {mode !== 'signin' && (
                  <small>
                    Use at least 8 characters. Your account’s password policy also applies.
                  </small>
                )}
              </label>
            )}
            {['verify', 'reset', 'otp'].includes(mode) && (
              <label>
                Verification code
                <input
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  required
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                />
              </label>
            )}
            <button
              className="button primary full"
              disabled={busy || !authConfigured}
              type="submit"
            >
              {busy
                ? 'Please wait…'
                : {
                    signin: 'Sign in',
                    signup: 'Create account',
                    verify: 'Verify email',
                    newPassword: 'Set password & continue',
                    otp: 'Confirm code',
                    forgot: 'Send reset code',
                    reset: 'Update password',
                  }[mode]}
              <ArrowRight size={18} />
            </button>
          </form>
          <div className="auth-links">
            {mode === 'signin' ? (
              <>
                <button className="text-button" onClick={() => changeMode('forgot')}>
                  Forgot password?
                </button>
                <p>
                  New to EcoGuard?{' '}
                  <button className="text-button" onClick={() => changeMode('signup')}>
                    Create an account
                  </button>
                </p>
                <button className="text-button muted" onClick={() => changeMode('verify')}>
                  Verify an existing account
                </button>
              </>
            ) : (
              <>
                <button className="text-button" onClick={() => changeMode('signin')}>
                  Back to sign in
                </button>
                {mode === 'verify' && (
                  <button
                    className="text-button"
                    disabled={busy || !authConfigured || !email}
                    onClick={() => void resend()}
                  >
                    Resend verification code
                  </button>
                )}
              </>
            )}
          </div>
          {config.devMode && (
            <div className="dev-login">
              <strong>LOCAL DEVELOPMENT · NOT CLOUD ACCEPTANCE</strong>
              <p>
                Use the local API adapter with isolated test identities. Authentication, AI, email
                and Lex remain unverified.
              </p>
              <div className="button-row">
                <button className="button secondary" onClick={() => auth.enterLocal('alice')}>
                  Continue as Alice
                </button>
                <button className="button secondary" onClick={() => auth.enterLocal('bob')}>
                  Continue as Bob
                </button>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
