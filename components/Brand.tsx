import { Link } from "@tanstack/react-router";

export function Brand({ to = "/", className = "" }: { to?: string; className?: string }) {
  return (
    <Link
      to={to}
      className={`group inline-flex items-center font-display text-lg font-bold tracking-tight ${className}`}
    >
      <span>RIDES4U</span>
    </Link>
  );
}
