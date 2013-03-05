Sensitivity Tests
=================


I tried tried to completely isolate the sensitivity tests from the core of the
sedfitter.

Every script do only specific functions to the sensitivity tests or general
usage of the sedfitter on data.


Current procedure description
-----------------------------

The current **main** script does sensitivity tests on fake data

* it creates a set of models from PADOVA isochrones and CK04 spectral library
  * it generates spectral models for a given sampling in age, mass, Z 
  * then a SED grid is generated by integrated through some filters after
  applying dust attenuation parameters Av, Rv, fbump

**note** Currently age, Z are not interpolated in the isochrones

* it creates fake SED dataset
  * it randomly extract models from the above model SED grid
  * add normal proportional noise on top of the fake data
  * apply a magnitude cut in F814W to simulate observational limits

* Finally, it fits the fake dataset and produces files on disk containing the
likelihood values, ans diagnostic plots.