import { num } from '../lib';

/** "1 lift", "3 lifts" */
export const count = (n: number, one: string, other: string) =>
  `${num(n)} ${n === 1 ? one : other}`;

const russian = new Intl.PluralRules('ru-RU');
/** Russian forms for 1, 2–4 and 5+: "1 выезд", "3 выезда", "5 выездов". */
export const ruWord = (n: number, one: string, few: string, many: string) => {
  const rule = russian.select(n);
  return rule === 'one' ? one : rule === 'many' ? many : few;
};
export const ruCount = (n: number, one: string, few: string, many: string) =>
  `${num(n)} ${ruWord(n, one, few, many)}`;
