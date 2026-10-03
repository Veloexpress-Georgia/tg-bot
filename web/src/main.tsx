import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/manrope/latin-400.css';
import '@fontsource/manrope/latin-500.css';
import '@fontsource/manrope/latin-600.css';
import '@fontsource/manrope/latin-700.css';
import '@fontsource/manrope/cyrillic-400.css';
import '@fontsource/manrope/cyrillic-500.css';
import '@fontsource/manrope/cyrillic-600.css';
import '@fontsource/manrope/cyrillic-700.css';
import '@fontsource/unbounded/latin-500.css';
import '@fontsource/unbounded/cyrillic-500.css';
import './styles/tokens.css';
import './styles/base.css';
import './ui/ui.css';
import './app/shell.css';
import App from './App';
import { currentLocale, useLocale } from './locale';
import { bootTelegram } from './telegram';
import { applyTheme, readThemePreference } from './theme';

bootTelegram();
applyTheme(readThemePreference());
document.documentElement.lang = currentLocale();
history.scrollRestoration = 'manual';

const client = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 20_000 },
  },
});

/** A language change remounts the cabinet, so every label is read again. */
function Localized() {
  return <App key={useLocale()} />;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <Localized />
    </QueryClientProvider>
  </StrictMode>,
);
