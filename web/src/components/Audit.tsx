import { Check, Clock3, ShieldCheck } from 'lucide-react';
import type { CommandResult } from '../types';
import { plain } from '../lib';
import { Empty } from './Shared';
const actions: Record<string, string> = {
  manual: 'Ручные места',
  payment: 'Отметка оплаты',
  cancel_lift: 'Отмена выезда',
  restore_lift: 'Восстановление выезда',
  cancel_day: 'Отмена дня',
  terms: 'Цена и дедлайн',
  plan: 'План выходных',
  post: 'Публикация опросов',
  extra: 'Дополнительный день',
  schedule: 'Расписание публикации',
  skip: 'Пропуск автопубликации',
  claim_payment: 'Сообщение об оплате',
  guest: 'Гостевые места',
  undo_payment: 'Отмена сообщения об оплате',
};
const statuses = {
  pending: 'Ожидает бота',
  running: 'Выполняется',
  complete: 'Готово',
  failed: 'Отклонено',
  review: 'Проверь результат',
};
export function AuditView({ entries, timezone }: { entries: CommandResult[]; timezone: string }) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">ПРОЗРАЧНОСТЬ ДЕЙСТВИЙ</span>
          <h2>Журнал кабинета</h2>
        </div>
        <ShieldCheck size={22} />
      </div>
      <p className="caption">
        Запросы из кабинета, их авторы и результаты. Операции с неопределённым результатом требуют
        проверки.
      </p>
      <div className="audit-list">
        {entries.map((entry) => (
          <div key={entry.id}>
            <span className={`audit-icon ${entry.status === 'complete' ? '' : 'audit-pending'}`}>
              {entry.status === 'complete' ? <Check size={18} /> : <Clock3 size={18} />}
            </span>
            <div>
              <strong>{actions[entry.action] ?? entry.action}</strong>
              <p>{entry.result ? plain(entry.result.message) : `Запрос #${entry.id}`}</p>
              <small>
                {new Date(entry.created_at).toLocaleString('ru-RU', {
                  timeZone: timezone,
                })}{' '}
                · {entry.actor_name ?? `Telegram ID ${entry.actor_user_id}`}
              </small>
            </div>
            <span
              className={`badge ${entry.status === 'failed' || entry.status === 'review' ? 'warning-badge' : ''}`}
            >
              {statuses[entry.status]}
            </span>
          </div>
        ))}
      </div>
      {!entries.length && <Empty text="Здесь появятся действия, выполненные через кабинет." />}
    </section>
  );
}
