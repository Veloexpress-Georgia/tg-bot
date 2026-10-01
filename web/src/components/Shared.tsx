import { Bike, Wallet } from 'lucide-react';
export function Metric({
  title,
  value,
  note,
  icon: Icon,
  accent = false,
}: {
  title: string;
  value: string;
  note: string;
  icon: typeof Wallet;
  accent?: boolean;
}) {
  return (
    <article className={`metric ${accent ? 'metric-accent' : ''}`}>
      <div className="metric-label">
        {title}
        <Icon size={18} />
      </div>
      <strong>{value}</strong>
      <span>{note}</span>
    </article>
  );
}

export function Empty({ text }: { text: string }) {
  return (
    <div className="empty-state">
      <Bike size={28} />
      <p>{text}</p>
    </div>
  );
}
