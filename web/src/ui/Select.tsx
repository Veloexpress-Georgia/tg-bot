import * as Primitive from '@radix-ui/react-select';
import { Check, ChevronDown, ChevronUp } from 'lucide-react';

export function Select<T extends string | number>({
  value,
  onValueChange,
  options,
  label,
  disabled = false,
}: {
  value: T;
  onValueChange(value: NoInfer<T>): void;
  options: { value: NoInfer<T>; label: string }[];
  label: string;
  disabled?: boolean;
}) {
  return (
    <Primitive.Root
      value={String(value)}
      disabled={disabled}
      onValueChange={(next) => {
        const selected = options.find((option) => String(option.value) === next);
        if (selected) onValueChange(selected.value);
      }}
    >
      <Primitive.Trigger className="select-trigger" aria-label={label}>
        <Primitive.Value />
        <Primitive.Icon className="select-chevron">
          <ChevronDown size={17} />
        </Primitive.Icon>
      </Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Content
          className="select-content"
          position="popper"
          sideOffset={6}
          collisionPadding={12}
        >
          <Primitive.ScrollUpButton className="select-scroll">
            <ChevronUp size={16} />
          </Primitive.ScrollUpButton>
          <Primitive.Viewport className="select-viewport">
            {options.map((option) => (
              <Primitive.Item
                className="select-item"
                key={option.value}
                value={String(option.value)}
              >
                <Primitive.ItemText>{option.label}</Primitive.ItemText>
                <Primitive.ItemIndicator className="select-indicator">
                  <Check size={16} />
                </Primitive.ItemIndicator>
              </Primitive.Item>
            ))}
          </Primitive.Viewport>
          <Primitive.ScrollDownButton className="select-scroll">
            <ChevronDown size={16} />
          </Primitive.ScrollDownButton>
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}
