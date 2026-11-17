import { useQuery } from '@tanstack/react-query';
import { ArrowUpRight, Send } from 'lucide-react';
import { ApiError, isAccessRefusal, loginRefusal, request } from '../../api';
import { Brand } from '../../app/Brand';
import { t } from '../../i18n';
import { telegram } from '../../telegram';
import { Button, Notice } from '../../ui';
import './login.css';

export function LoginPage({ error, retry }: { error: Error | null; retry(): void }) {
  const config = useQuery({
    queryKey: ['config'],
    queryFn: () => request<{ browser_login: boolean }>('/api/config'),
    retry: false,
  });
  const inTelegram = !!telegram();
  const signedOut = error instanceof ApiError && error.status === 401;
  // Refused by the server: after a browser sign-in redirect, or on an existing session.
  const refusal =
    loginRefusal(window.location.search) ?? (isAccessRefusal(error) ? error?.message : undefined);
  return (
    <div className="login">
      <div className="login-card">
        <Brand />
        <h1>{t.login.title}</h1>
        <p className="login-text">{t.login.text}</p>
        {inTelegram ? (
          <>
            <Notice tone={signedOut ? 'info' : 'danger'}>
              {signedOut ? t.login.reopen : (error?.message ?? t.errors.generic)}
            </Notice>
            <Button variant="primary" size="lg" block onClick={retry}>
              {t.common.retry}
            </Button>
          </>
        ) : (
          <>
            {config.data?.browser_login ? (
              <a className="btn btn-primary btn-lg btn-block" href="/api/auth/login">
                <Send size={18} />
                {t.login.withTelegram}
              </a>
            ) : (
              <Notice>{t.login.openFromBot}</Notice>
            )}
            {refusal && <Notice tone="danger">{refusal}</Notice>}
            {error && !signedOut && !refusal && (
              <Notice tone="danger">
                <span className="login-error">
                  {t.login.connectionFailed}
                  <Button size="sm" onClick={retry}>
                    {t.common.retry}
                  </Button>
                </span>
              </Notice>
            )}
          </>
        )}
        <a className="login-demo" href="/?demo=1">
          {t.login.demo}
          <ArrowUpRight size={15} />
        </a>
      </div>
    </div>
  );
}
