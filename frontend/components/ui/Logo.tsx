import type { CSSProperties, ImgHTMLAttributes } from "react";

type LogoProps = Omit<ImgHTMLAttributes<HTMLImageElement>, "src" | "alt" | "width" | "height"> & {
  alt?: string;
  size?: number;
};

export default function Logo({ alt = "HireWiz logo", className, size = 34, style, ...props }: LogoProps) {
  const dimensions: CSSProperties = {
    width: size,
    height: size,
    ...style,
  };

  return (
    <img
      src="/logo.png"
      alt={alt}
      width={size}
      height={size}
      className={`rounded-lg object-contain ${className ?? ""}`}
      style={dimensions}
      {...props}
    />
  );
}
