class ConversionStats:
    def __init__(self):
        self.total = 0
        self.processed = 0
        self.success = 0
        self.failure = 0
        self.skipped = 0
        self.current_phase = ""
        # Track stats per phase
        self.phase_stats = {
            "Preprocessing": {"total": 0, "processed": 0, "success": 0, "failure": 0, "skipped": 0},
            "XML Processing": {"total": 0, "processed": 0, "success": 0, "failure": 0, "skipped": 0, "body_links_success": 0, "body_links_missing": 0},
            "Converting": {"total": 0, "processed": 0, "success": 0, "failure": 0, "skipped": 0},
            "Blog Posts": {"total": 0, "processed": 0, "success": 0, "failure": 0, "skipped": 0},
            "Fixing links": {"total": 0, "processed": 0, "success": 0, "failure": 0, "skipped": 0, "links_fixed": 0}
        }

        # Add XML extraction stats
        self.xml_stats = {
            "users_extracted": 0,
            "spaces_extracted": 0,
            "pages_extracted": 0,
            "attachments_extracted": 0,
            "comments_extracted": 0
        }

    def update_progress(self):
        """Update progress in terminal"""
        if self.current_phase:
            if self.current_phase == "Preprocessing":
                print(f"\rPhase completed - {self.current_phase}", end='', flush=True)
            else:
                # Include skipped files in the display
                total_processed = self.processed + self.skipped
                print(f"\r{total_processed}/{self.total} completed - {self.current_phase}", end='', flush=True)
        else:
            print(f"\r{self.processed}/{self.total} completed", end='', flush=True)

    def update_xml_stats(self, stat_name, count):
        """Update XML extraction statistics"""
        if stat_name in self.xml_stats:
            self.xml_stats[stat_name] += count

    def set_phase(self, phase: str):
        """Set current processing phase and reset counters"""
        self.total = 0
        self.processed = 0
        self.success = 0
        self.failure = 0
        self.skipped = 0
        self.current_phase = phase
        # Initialize phase stats if not already present
        if phase not in self.phase_stats:
            self.phase_stats[phase] = {"total": 0, "processed": 0, "success": 0, "failure": 0, "skipped": 0}
        self.update_progress()

    def update_phase_stats(self):
        """Update stats for the current phase"""
        if self.current_phase:
            self.phase_stats[self.current_phase]["total"] = self.total
            self.phase_stats[self.current_phase]["processed"] = self.processed
            self.phase_stats[self.current_phase]["success"] = self.success
            self.phase_stats[self.current_phase]["failure"] = self.failure
            self.phase_stats[self.current_phase]["skipped"] = self.skipped

    def increment_links_fixed(self, count=1):
        """Increment the count of links fixed in the current phase"""
        if self.current_phase == "Fixing links" and "links_fixed" in self.phase_stats[self.current_phase]:
            self.phase_stats[self.current_phase]["links_fixed"] += count

    def increment_body_links_stats(self, success_count, missing_count):
        """Increment the body links success and missing counts in the XML Processing phase"""
        if "XML Processing" in self.phase_stats:
            self.phase_stats["XML Processing"]["body_links_success"] += success_count
            self.phase_stats["XML Processing"]["body_links_missing"] += missing_count

    def skip_file(self, phase="Preprocessing"):
        """Track a skipped file"""

        if phase is None:
            phase = self.current_phase

        # Ensure the phase exists in phase_stats
        if phase not in self.phase_stats:
            self.phase_stats[phase] = {"total": 0, "processed": 0, "success": 0, "failure": 0, "skipped": 0}
            
        # Increment the skipped count for this phase
        self.phase_stats[phase]["skipped"] += 1

        # If we're in the current phase, also update the instance variable
        if phase == self.current_phase:
            self.skipped += 1
        
    def print_final_report(self):
        """Print final statistics by phase"""
        report = "- Conversion Summary -"

        # Add XML extraction stats if any are non-zero
        if hasattr(self, 'xml_stats') and any(v > 0 for v in self.xml_stats.values()):
            report += "\n\nXML Extraction:"
            for stat_name, count in self.xml_stats.items():
                if count > 0:
                    # Convert snake_case to Title Case for display
                    display_name = ' '.join(word.capitalize() for word in stat_name.split('_'))
                    report += f"\n  {display_name}: {count}"

        # Print stats for each phase
        for phase, stats in self.phase_stats.items():
            if any(v > 0 for k, v in stats.items()):  # Show phases with any activity
                phase_report = f"\n\n{phase}:"
                if stats["total"] > 0 or stats["processed"] > 0:
                    if phase == "Fixing links":
                        phase_report += f"\n  Files Processed: {stats['total'] if stats['total'] > 0 else stats['processed']}"
                    else:
                        phase_report += f"\n  Processed: {stats['total'] if stats['total'] > 0 else stats['processed']}"
                    phase_report += f"\n  Success: {stats['success']}"
                    phase_report += f"\n  Failure: {stats['failure']}"
                    phase_report += f"\n  Skipped: {stats['skipped']}"
                
                # Add special stats for specific phases
                if phase == "Fixing links" and "links_fixed" in stats:
                    phase_report += f"\n  Links Processed: {stats['links_fixed']}"
                elif phase == "XML Processing":
                    if stats["body_links_success"] > 0:
                        pass
                        #phase_report += f"\n  Body Links Success: {stats['body_links_success']}"
                    if stats["body_links_missing"] > 0:
                        pass
                        #phase_report += f"\n  Body Links Missing: {stats['body_links_missing']}"
                
                report += phase_report

        report += f"\n\nSee log file for details."
        print(report)

        # Return the report for logging
        return report
