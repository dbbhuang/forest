'''Headers for CSVs handled by powrep.

'''

raw_header = {
    'iOS': [ # Column names for raw Beiwe power state files from iPhones.
        'timestamp', # Millisecond timestamp.
        'UTC time',  # Human-readable UTC date-time formatted as '%Y-%m-%dT%H:%M:%S.%f'.        
        'event', # Name of the event.  See README.md for documentation.
        'level'  # Battery level.
        ],
    'Android': [ # Column names for raw Beiwe power state files from Android phones.
        'timestamp', # Millisecond timestamp.
        'UTC time',  # Human-readable UTC date-time formatted as '%Y-%m-%dT%H:%M:%S.%f'.
        'event' # Name of the event.  See README.md for documentation.
        ]
    }


keep_header = {
    'iOS': [
        'timestamp', # Millisecond timestamp.
        'event', # Name of the event.  See README.md for documentation.
        'level'  # Battery level.
        ],
    'Android': [
        'timestamp', # Millisecond timestamp.
        'event' # Name of the event.  See README.md for documentation.
        ]
    }


summary_header = [ # Column names for powrep.summary output.
  'user_id', # Beiwe User ID.
  'os',      # 'Android' or 'iOS'.
  'n_observations',          # Number of events observed for this user.
  'n_files',                 # Number of raw power state files for this user.
  'first_file', 'last_file', # Basenames of first and last hourly files.
  'unknown_headers', # Number of files with unrecognized headers.
  'unknown_events'   # Number of unique unknown event categories.
   ]


extract_header = {
'iOS': [ # Column names for extracted event variables for iPhones.
        'timestamp', # Millisecond timestamp.
        'value',     # Value of the event variable.    
        'battery_level', # Battery level, from 0.0 (drained) to 1.0 (fully charged).
        ],
'Android': [ # Column names for extracted event variables for Android phones.
            'timestamp', # Millisecond timestamp.
            'value',     # Value of the event variable.    
            ]
}
    