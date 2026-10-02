import * as Primitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  sheet = false,
}: {
  open: boolean;
  onOpenChange(open: boolean): void;
  title: string;
  description?: string;
  children: ReactNode;
  sheet?: boolean;
}) {
  return (
    <Primitive.Root open={open} onOpenChange={onOpenChange}>
      <Primitive.Portal>
        <Primitive.Overlay className="dialog-overlay" />
        <Primitive.Content className={`dialog-content ${sheet ? 'dialog-sheet' : ''}`}>
          <div className="dialog-heading">
            <div>
              <Primitive.Title>{title}</Primitive.Title>
              <Primitive.Description>
                {description ?? 'Информация и действия для выбранного выезда.'}
              </Primitive.Description>
            </div>
            <Primitive.Close className="button button-icon button-ghost" aria-label="Закрыть">
              <X size={20} />
            </Primitive.Close>
          </div>
          <div className="dialog-body">{children}</div>
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}
