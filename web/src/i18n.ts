import { currentLocale, setCurrentLocale, subscribeLocale, type Locale } from './locale';
import { en, type Messages } from './locales/en';
import { ru } from './locales/ru';

export type { Messages };
const dictionaries: Record<Locale, Messages> = { en, ru };

/** The active dictionary. The app remounts on a language change, so reading it is enough. */
export let t: Messages = dictionaries[currentLocale()];

subscribeLocale(() => {
  t = dictionaries[currentLocale()];
  document.documentElement.lang = currentLocale();
});

export function setLocale(next: Locale) {
  setCurrentLocale(next);
}
export { useLocale } from './locale';
export type { Locale } from './locale';
