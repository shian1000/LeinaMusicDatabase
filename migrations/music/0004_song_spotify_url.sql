-- A song's Spotify page, set by hand (Song actions -> "Set Spotify link") for
-- a release Spotify's own search never surfaces - e.g. Cypis' 2016 album
-- "Sprawdzian Z Chemii", buried under dozens of newer singles. Either an
-- album or a track URL. "Fill missing data -> Years" reads the release date
-- straight off this page before trying any discovery module.
ALTER TABLE songs ADD COLUMN spotify_url VARCHAR;
