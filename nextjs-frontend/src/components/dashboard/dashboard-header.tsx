'use client';

import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { LayoutDashboard, User, Shield, ShieldCheck, LogOut, House } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { logoutAction } from '@/app/actions/auth-actions';
import { siteConfig } from '@/config/site';
import { SITE_HOME_HREF, backToSiteLabel } from './nav-items';

interface DashboardHeaderProps {
  user: {
    email?: string | null;
    dbDisplayName?: string | null;
    dbAvatarUrl?: string | null;
    /**
     * The backend profile says this account is an admin. It only decides whether the menu shows the link: the
     * `/admin` layout and every admin action check admin access on the server and are the real gate.
     */
    isAdmin?: boolean;
  };
}

export function DashboardHeader({ user }: DashboardHeaderProps) {
  const handleLogout = async () => {
    await logoutAction();
    window.location.href = '/login';
  };

  return (
    <div className="flex items-center gap-4">
      {/* Notifications dropdown: switched off until delivers notifications through the API. */}

      {/* User Menu */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="relative h-11 w-11 rounded-full hover:bg-accent">
            <Avatar className="h-11 w-11 ring-2 ring-transparent transition-all hover:ring-theme-accent/50">
              <AvatarImage src={user.dbAvatarUrl || undefined} alt={user.email || 'User'} />
              <AvatarFallback>{user.email?.charAt(0).toUpperCase() || 'U'}</AvatarFallback>
            </Avatar>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-64" align="end" forceMount>
          <DropdownMenuLabel>
            <div className="flex flex-col space-y-2 py-1.5">
              <p className="text-sm font-bold leading-none">{user.dbDisplayName || 'User'}</p>
              <p className="text-xs leading-none text-muted-foreground/80">{user.email}</p>
            </div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem asChild>
            <Link href="/dashboard" className="cursor-pointer">
              <LayoutDashboard className="mr-2 h-4 w-4" />
              <span>Dashboard</span>
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link href="/dashboard/profile" className="cursor-pointer">
              <User className="mr-2 h-4 w-4" />
              <span>Profile</span>
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link href="/dashboard/security" className="cursor-pointer">
              <Shield className="mr-2 h-4 w-4" />
              <span>Security</span>
            </Link>
          </DropdownMenuItem>
          {user.isAdmin === true && (
            <DropdownMenuItem asChild>
              <Link href="/admin" className="cursor-pointer">
                <ShieldCheck className="mr-2 h-4 w-4" />
                <span>Admin</span>
              </Link>
            </DropdownMenuItem>
          )}
          <DropdownMenuItem asChild>
            <Link href={SITE_HOME_HREF} className="cursor-pointer">
              <House className="mr-2 h-4 w-4" />
              <span>{backToSiteLabel(siteConfig.name)}</span>
            </Link>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={handleLogout}
            className="cursor-pointer text-destructive focus:text-destructive"
          >
            <LogOut className="mr-2 h-4 w-4" />
            <span>Log out</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
