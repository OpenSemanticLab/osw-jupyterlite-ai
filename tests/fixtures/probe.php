<?php
// TEMPORARY verification probe - delete after use.
// Reports only cookie NAMES, never values, so nothing sensitive is echoed. Exists to
// answer one question: does a request from the Pyodide kernel carry this origin's
// cookies?
header( 'Content-Type: application/json' );
header( 'Cache-Control: no-store' );
echo json_encode( [
	'cookie_names' => array_keys( $_COOKIE ),
	'has_probe'    => isset( $_COOKIE['osw_sandbox_probe'] ),
	'ua_is_worker' => !isset( $_SERVER['HTTP_SEC_FETCH_DEST'] ) ? 'unknown' : $_SERVER['HTTP_SEC_FETCH_DEST'],
] );
