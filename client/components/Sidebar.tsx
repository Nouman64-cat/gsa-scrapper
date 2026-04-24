"use client";

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { 
  LayoutDashboard, 
  Package, 
  Link2, 
  Settings, 
  HelpCircle,
  ChevronRight,
  Bot
} from 'lucide-react';

const navItems = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Parts Management', href: '/parts', icon: Package },
  { name: 'Links Management', href: '/links', icon: Link2 },
];

const secondaryItems = [
  { name: 'Settings', href: '/settings', icon: Settings },
  { name: 'Support', href: '/support', icon: HelpCircle },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-72 h-screen sticky top-0 bg-slate-900 text-slate-300 flex flex-col border-r border-slate-800 shadow-2xl">
      <div className="p-8">
        <div className="flex items-center gap-3 px-2">
          <div className="bg-blue-600 p-2 rounded-xl shadow-lg shadow-blue-500/20">
            <Bot className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-xl font-bold text-white tracking-tight">GSA Scraper</h2>
            <p className="text-xs text-slate-500 font-medium uppercase tracking-widest">Control Center</p>
          </div>
        </div>
      </div>

      <nav className="flex-1 px-4 space-y-1">
        <p className="px-4 text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-4">Main Menu</p>
        {navItems.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.name}
              href={item.href}
              className={`flex items-center justify-between group px-4 py-3 rounded-xl transition-all duration-200 ${
                isActive 
                  ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/20' 
                  : 'hover:bg-slate-800 hover:text-white'
              }`}
            >
              <div className="flex items-center gap-3">
                <item.icon className={`w-5 h-5 ${isActive ? 'text-white' : 'text-slate-400 group-hover:text-blue-400'}`} />
                <span className="font-semibold text-sm">{item.name}</span>
              </div>
              <ChevronRight className={`w-4 h-4 transition-transform duration-200 ${isActive ? 'opacity-100 translate-x-0' : 'opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0'}`} />
            </Link>
          );
        })}
      </nav>

      <div className="px-4 py-8 space-y-1">
        <p className="px-4 text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-4">Support</p>
        {secondaryItems.map((item) => (
          <Link
            key={item.name}
            href={item.href}
            className="flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white transition-all duration-200"
          >
            <item.icon className="w-5 h-5" />
            <span className="font-semibold text-sm">{item.name}</span>
          </Link>
        ))}

        <div className="mt-8 px-4 py-6 bg-slate-800/50 rounded-2xl border border-slate-700/50">
          <div className="flex items-center gap-3 mb-3">
             <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
             <span className="text-xs font-bold text-slate-300">System Live</span>
          </div>
          <p className="text-[11px] text-slate-500 leading-relaxed">
            All systems are operational. Parallel workers are ready for task allocation.
          </p>
        </div>
      </div>
    </aside>
  );
}
