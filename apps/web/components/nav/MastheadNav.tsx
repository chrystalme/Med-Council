import { UserButton } from "@clerk/nextjs";
import Link from "next/link";
import { Folder, NotebookPen } from "lucide-react";
import type { ReactNode } from "react";

type Destination = "workspace" | "patient";

export type MastheadNavProps = {
  active: Destination;
  plateLabel: ReactNode;
  backLink: { href: "/case" | "/patient"; label: ReactNode };
  userIdSuffix: string;
};

export function MastheadNav({
  active,
  plateLabel,
  backLink,
  userIdSuffix,
}: MastheadNavProps) {
  return (
    <header className="px-6 md:px-14 pt-7 pb-5 flex items-center justify-between border-b border-line">
      <Link href="/" className="flex items-baseline gap-4 group">
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="block h-2.5 w-2.5 rounded-sm bg-indigo rotate-45 group-hover:bg-cornflower transition-colors"
          />
          <span className="font-display text-[1.25rem] tracking-tight text-ink">
            MedAI Council
          </span>
        </div>
        <span className="mono-label hidden sm:inline">{plateLabel}</span>
      </Link>

      <div className="flex items-center gap-5">
        {/* Portrait-phone icon row (< sm). Mutually exclusive with the text back-link below. */}
        <nav aria-label="Primary" className="flex items-center gap-1 sm:hidden">
          <IconLink
            href="/case"
            label="Workspace"
            icon={<NotebookPen className="h-5 w-5" aria-hidden />}
            isActive={active === "workspace"}
          />
          <IconLink
            href="/patient"
            label="Patient file"
            icon={<Folder className="h-5 w-5" aria-hidden />}
            isActive={active === "patient"}
          />
        </nav>

        {/* sm+ back-link (preserves today's per-page wording) */}
        <Link
          href={backLink.href}
          className="mono-label hover:text-indigo transition-colors hidden sm:inline"
        >
          {backLink.label}
        </Link>

        <span className="mono-label hidden md:inline">
          Attending <span className="diamond" /> {userIdSuffix}
        </span>
        <UserButton
          appearance={{
            elements: {
              avatarBox: "h-9 w-9 border border-line-strong rounded-full",
            },
          }}
        />
      </div>
    </header>
  );
}

type IconLinkProps = {
  href: "/case" | "/patient";
  label: string;
  icon: ReactNode;
  isActive: boolean;
};

function IconLink({ href, label, icon, isActive }: IconLinkProps) {
  return (
    <Link
      href={href}
      aria-label={label}
      aria-current={isActive ? "page" : undefined}
      className={[
        "inline-flex items-center justify-center h-10 w-10 transition-colors",
        isActive
          ? "text-indigo border-b-2 border-indigo -mb-[1px]"
          : "text-ink-muted hover:text-ink",
      ].join(" ")}
    >
      {icon}
    </Link>
  );
}
