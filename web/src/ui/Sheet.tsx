import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';
import { t } from '../i18n';

/**
 * The only modal surface: a bottom sheet on phones, a centred dialog on wide
 * screens. It holds a short task (confirm, record a payment, settings), never
 * another workspace.
 */
export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
}: {
  open: boolean;
  onOpenChange(open: boolean): void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="sheet-overlay" />
        <Dialog.Content
          className="sheet"
          {...(description ? {} : { 'aria-describedby': undefined })}
        >
          <div className="sheet-head">
            <div>
              <Dialog.Title className="sheet-title">{title}</Dialog.Title>
              {description && (
                <Dialog.Description className="sheet-desc">{description}</Dialog.Description>
              )}
            </div>
            <Dialog.Close className="btn btn-ghost btn-icon" aria-label={t.common.close}>
              <X size={20} />
            </Dialog.Close>
          </div>
          {children && <div className="sheet-body scroll-clean">{children}</div>}
          {footer && <div className="sheet-foot">{footer}</div>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
