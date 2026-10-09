import type { ButtonHTMLAttributes } from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/utils'

export const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-lg text-sm font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50 disabled:cursor-not-allowed',
  { variants: { variant: {
    default: 'bg-accent text-[#071a16] hover:bg-[#80f4c9]',
    secondary: 'bg-surface border border-line text-[#cfdef0] hover:bg-[#21344d]',
    ghost: 'text-muted hover:text-white hover:bg-white/5',
  }, size: { default: 'px-4 py-2.5', sm: 'px-3 py-1.5', icon: 'h-9 w-9' } },
  defaultVariants: { variant: 'default', size: 'default' } },
)

type Props = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>
export function Button({ className, variant, size, type = 'button', ...props }: Props) {
  return <button type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
