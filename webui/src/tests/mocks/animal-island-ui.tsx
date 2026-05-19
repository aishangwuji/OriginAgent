import * as React from "react";

type IslandButtonProps = Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type"> & {
  block?: boolean;
  htmlType?: "button" | "submit" | "reset";
  icon?: React.ReactNode;
  loading?: boolean;
  size?: "small" | "middle" | "large";
  type?: "primary" | "default" | "dashed" | "text" | "link";
};

export function Button({
  block: _block,
  htmlType = "button",
  icon,
  loading,
  size: _size,
  type: _type,
  disabled,
  children,
  ...props
}: IslandButtonProps) {
  return (
    <button type={htmlType} disabled={disabled || loading} {...props}>
      {icon}
      {children}
    </button>
  );
}

type IslandInputProps = React.InputHTMLAttributes<HTMLInputElement> & {
  allowClear?: boolean;
  prefix?: React.ReactNode;
  onClear?: () => void;
  shadow?: boolean;
  size?: "small" | "middle" | "large";
  status?: "error" | "warning";
};

export function Input({
  prefix,
  allowClear,
  onClear,
  shadow: _shadow,
  size: _size,
  status: _status,
  ...props
}: IslandInputProps) {
  return (
    <span>
      {prefix}
      <input {...props} />
      {allowClear ? (
        <button type="button" aria-label="clear" onClick={onClear}>
          x
        </button>
      ) : null}
    </span>
  );
}

export function Card({ children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div {...props}>{children}</div>;
}

export function Typewriter({ children }: { children?: React.ReactNode }) {
  return <>{children}</>;
}
