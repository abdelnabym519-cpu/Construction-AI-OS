// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
import clsx from 'clsx';

import { APP_NAME } from '@/shared/lib/brandName';

interface LogoProps {
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl';
  animate?: boolean;
  className?: string;
}

/* Icon sizes — compact so the text dominates */
const sizeMap = {
  xs: 'h-5 w-5',
  sm: 'h-6 w-6',
  md: 'h-7 w-7',
  lg: 'h-10 w-10',
  xl: 'h-14 w-14',
};

/**
 * Brand mark — a neutral placeholder: white "C" monogram on the platform
 * gradient (#0066ff → #5856d6). The public/logo.svg, public/favicon.svg and
 * public/pwa/icon-*.svg files carry the same mark; a real designed identity
 * replaces all of them together (see NOTICE for what shipped before).
 *
 * `animate` triggers a two-step entrance:
 *   1. Background scales in
 *   2. Monogram fades in
 */
export function Logo({ size = 'md', animate = false, className }: LogoProps) {
  const gradientId = `oe-lg-${size}-${animate ? 'a' : 's'}`;

  const monogramStyle = animate
    ? {
        animation: `oeWindowFade 500ms ease both`,
        animationDelay: '250ms',
      }
    : undefined;

  const bgStyle = animate
    ? {
        animation: `oeBgScale 450ms cubic-bezier(0.34,1.56,0.64,1) both`,
      }
    : undefined;

  return (
    <div
      className={clsx(sizeMap[size], 'relative shrink-0', className)}
      style={animate ? { animation: 'oeLogoFloat 3s ease-in-out 1.2s infinite, oeLogoGlow 3s ease-in-out 1.2s infinite' } : undefined}
    >
      <svg viewBox="0 0 512 512" fill="none" xmlns="http://www.w3.org/2000/svg" className="w-full h-full">
        <defs>
          <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#0066ff" />
            <stop offset="100%" stopColor="#5856d6" />
          </linearGradient>
        </defs>

        {/* Background */}
        <rect x="32" y="32" width="448" height="448" rx="96" fill={`url(#${gradientId})`} style={bgStyle} />

        {/* Monogram: an open ring reading as "C" */}
        <path
          d="M333 164 A 120 120 0 1 0 333 348"
          stroke="#fff"
          strokeWidth="56"
          strokeLinecap="round"
          fill="none"
          opacity=".95"
          style={monogramStyle}
        />
      </svg>
    </div>
  );
}

/* ── LogoWithText ──────────────────────────────────────────────────────── */

interface LogoWithTextProps extends LogoProps {
  showVersion?: boolean;
}

/* Text sizes — larger than icon to make the name prominent */
const textSizeMap = {
  xs: 'text-[15px] leading-none',
  sm: 'text-[16px] leading-none',
  md: 'text-[17px] leading-none',
  lg: 'text-xl leading-none',
  xl: 'text-2xl leading-none',
};

const gapSizeMap = {
  xs: 'gap-1.5',
  sm: 'gap-2',
  md: 'gap-2',
  lg: 'gap-2.5',
  xl: 'gap-3',
};

/**
 * Logo + product name. The name renders from the ONE frontend display
 * constant, APP_NAME in '@/shared/lib/brandName', which the backend test
 * test_the_display_name_is_one_constant.py holds equal to the backend
 * app_name. The wordmark is never retyped here.
 * - Font: Plus Jakarta Sans 800
 * - Letter-spacing: -0.02em
 * - Icon is compact, name is prominent
 * - `showVersion` is accepted for API compatibility and no longer changes
 *   the output: the display name renders whole, because a wordmark that
 *   could be truncated token by token is exactly how the name used to drift.
 */
export function LogoWithText({ size = 'md', animate, showVersion: _showVersion = true, className }: LogoWithTextProps) {
  return (
    <div className={clsx('flex items-center', gapSizeMap[size], className)}>
      <Logo size={size} animate={animate} />
      <span
        className={clsx(
          textSizeMap[size],
          'font-medium text-content-primary whitespace-nowrap tracking-tight',
        )}
        style={{ fontFamily: "'Plus Jakarta Sans', system-ui, sans-serif", letterSpacing: '-0.02em' }}
      >
        {APP_NAME}
      </span>
    </div>
  );
}
